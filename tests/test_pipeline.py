from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from trading_mvp.config import get_settings as get_app_settings
from trading_mvp.models import (
    AgentRun,
    AuditEvent,
    Execution,
    FeatureSnapshot,
    MarketSnapshot,
    Order,
    PendingEntryPlan,
    PnLSnapshot,
    Position,
    RiskCheck,
    SchedulerRun,
    User,
)
from trading_mvp.providers import ProviderResult
from trading_mvp.schemas import (
    AIPriorContextPacket,
    DerivativesContextPayload,
    EventContextPayload,
    FeaturePayload,
    MarketCandle,
    MarketSnapshotPayload,
    RegimeFeatureContext,
    RiskCheckResult,
    TimeframeFeatureContext,
    TradeDecision,
    TradeDecisionCandidate,
    TradeDecisionCandidateScore,
)
from trading_mvp.services.agents import MarketSettingsAdvisorAgent, TradingDecisionAgent
from trading_mvp.services.binance import BinanceAPIError
from trading_mvp.services.execution import execute_live_trade, sync_live_state
from trading_mvp.services.features import compute_features
from trading_mvp.services.market_data import build_market_snapshot
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.runtime_state import mark_sync_success, summarize_runtime_state
from trading_mvp.services.scheduler import (
    is_interval_decision_due,
    maybe_refresh_exchange_sync_freshness,
    run_due_entry_plan_watcher_cycle,
    run_due_windows,
    run_exchange_sync_cycle,
    run_interval_decision_cycle,
    run_market_refresh_cycle,
    run_position_management_cycle,
    run_window,
)
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.seed import seed_demo_data
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_pipeline_sync_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


def _pre_ai_gate_snapshot() -> MarketSnapshotPayload:
    snapshot_time = utcnow_naive()
    return MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=3,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=2), open=69950.0, high=70040.0, low=69920.0, close=70000.0, volume=900.0),
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=1), open=70000.0, high=70070.0, low=69980.0, close=70030.0, volume=980.0),
            MarketCandle(timestamp=snapshot_time, open=70030.0, high=70090.0, low=70010.0, close=70060.0, volume=1020.0),
        ],
    )


def _pre_ai_gate_feature(
    *,
    volume_ratio: float,
    weak_volume: bool,
    volume_regime: str = "normal",
    event_context: EventContextPayload | None = None,
) -> FeaturePayload:
    feature_payload = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.32,
        volatility_pct=0.002,
        volume_ratio=volume_ratio,
        drawdown_pct=0.001,
        rsi=58.0,
        atr=90.0,
        atr_pct=0.0013,
        momentum_score=0.18,
        multi_timeframe={
            "15m": TimeframeFeatureContext(
                timeframe="15m",
                trend_score=0.32,
                volatility_pct=0.002,
                volume_ratio=volume_ratio,
                drawdown_pct=0.001,
                rsi=58.0,
                atr=90.0,
                atr_pct=0.0013,
                momentum_score=0.18,
            )
        },
        regime=RegimeFeatureContext(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            volume_regime=volume_regime,
            momentum_state="stable",
            weak_volume=weak_volume,
            momentum_weakening=False,
        ),
    )
    if event_context is not None:
        return feature_payload.model_copy(update={"event_context": event_context})
    return feature_payload


def _pre_ai_gate_macro_event_context(*, minutes_to_event: int = 10) -> EventContextPayload:
    generated_at = utcnow_naive()
    return EventContextPayload(
        source_status="external_api",
        source_provenance="external_api",
        source_vendor="fred",
        generated_at=generated_at,
        is_stale=False,
        is_complete=True,
        next_event_at=generated_at + timedelta(minutes=minutes_to_event),
        next_event_name="US CPI",
        next_event_importance="high",
        minutes_to_next_event=minutes_to_event,
        active_risk_window=True,
        affected_assets=["USD", "CRYPTO"],
        event_bias="bearish",
        enrichment_vendors=["bls"],
        events=[],
    )


def _pre_ai_gate_open_position() -> Position:
    return Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=70060.0,
        leverage=2.0,
        stop_loss=69400.0,
        take_profit=71200.0,
        realized_pnl=0.0,
        unrealized_pnl=6.0,
        metadata_json={},
    )


class _AllowAiGate:
    allowed = True
    reason = "allowed"
    retry_after_seconds = 0

    def as_metadata(self) -> dict[str, object]:
        return {"allowed": True, "reason": "allowed"}


class _CountingDecisionProvider:
    name = "openai"

    def __init__(self, decision: str = "hold") -> None:
        self.calls = 0
        self.decision = decision

    def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
        del role, response_model, instructions
        self.calls += 1
        latest_price = float(payload["market_snapshot"]["latest_price"])
        return ProviderResult(
            provider="openai",
            output={
                "decision": self.decision,
                "confidence": 0.62,
                "symbol": payload["market_snapshot"]["symbol"],
                "timeframe": payload["market_snapshot"]["timeframe"],
                "entry_mode": "none",
                "max_holding_minutes": 120,
                "risk_pct": 0.01,
                "leverage": 1.0,
                "rationale_codes": ["TEST_PROVIDER_CALLED"],
                "no_trade_reason_codes": ["TEST_PROVIDER_CALLED"] if self.decision == "hold" else [],
                "scenario_note": f"Provider observed {latest_price:.1f}",
                "explanation_short": f"provider {self.decision}",
                "explanation_detailed": "Provider was intentionally called for the pre-AI gate control case.",
            },
        )


class _RoutingAdvisorDecisionProvider:
    name = "openai"

    def __init__(self, decision: str = "hold", advisor_profile: str = "CAUTION") -> None:
        self.decision = decision
        self.advisor_profile = advisor_profile
        self.trading_calls = 0
        self.advisor_calls = 0
        self.trading_payloads: list[dict[str, object]] = []

    def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
        del response_model, instructions
        if role == "market_settings_advisor":
            self.advisor_calls += 1
            generated_at = datetime.now(UTC)
            return ProviderResult(
                provider="openai",
                output={
                    "recommendation_id": "advisor-before-trading-ai-001",
                    "generated_at": generated_at.isoformat(),
                    "valid_until": (generated_at + timedelta(minutes=15)).isoformat(),
                    "symbol_scope": ["BTCUSDT"],
                    "recommended_profile_id": self.advisor_profile,
                    "confidence": 0.86,
                    "reason_summary": "Refresh execution risk profile before trading AI.",
                    "reason_codes": ["PRE_TRADE_PROFILE_REFRESH"],
                    "observed_risk_flags": ["HIGH_VOLATILITY"],
                    "suggested_new_entry_policy": "STRICT_CONFIRMATION_ONLY",
                    "do_not_relax": True,
                },
                usage={"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
                model="gpt-4.1-mini",
            )
        self.trading_calls += 1
        self.trading_payloads.append(dict(payload))
        return ProviderResult(
            provider="openai",
            output={
                "decision": self.decision,
                "confidence": 0.62,
                "symbol": payload["market_snapshot"]["symbol"],
                "timeframe": payload["market_snapshot"]["timeframe"],
                "entry_mode": "none",
                "max_holding_minutes": 120,
                "risk_pct": 0.01,
                "leverage": 1.0,
                "rationale_codes": ["TEST_PROVIDER_CALLED"],
                "no_trade_reason_codes": ["TEST_PROVIDER_CALLED"] if self.decision == "hold" else [],
                "scenario_note": "Trading AI received advisor context.",
                "explanation_short": f"provider {self.decision}",
                "explanation_detailed": "Provider was called after the profile advisor refresh.",
            },
            usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            model="gpt-4.1-mini",
        )


def _entry_plan_test_decision() -> TradeDecision:
    return TradeDecision(
        decision="long",
        confidence=0.72,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=69000.0,
        entry_zone_max=69300.0,
        entry_mode="pullback_confirm",
        invalidation_price=68500.0,
        max_chase_bps=25.0,
        idea_ttl_minutes=15,
        stop_loss=68500.0,
        take_profit=71000.0,
        max_holding_minutes=180,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["ALIGNED_PULLBACK", "TREND_UP"],
        explanation_short="pipeline entry-plan test",
        explanation_detailed="Entry-plan pipeline test uses a deterministic pullback-confirm decision.",
    )


def _prepare_live_entry_plan_settings(db_session):
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()
    return settings_row


def _entry_plan_fake_evaluate_risk(session, settings_row, decision, market_snapshot, **kwargs):
    trigger_waiting = decision.decision in {"long", "short"} and decision.entry_mode != "immediate"
    blocked_reason_codes = ["ENTRY_TRIGGER_NOT_MET"] if trigger_waiting else []
    result = RiskCheckResult(
        allowed=not trigger_waiting,
        decision=decision.decision,
        reason_codes=blocked_reason_codes,
        blocked_reason_codes=blocked_reason_codes,
        adjustment_reason_codes=[],
        approved_risk_pct=0.0 if trigger_waiting else float(decision.risk_pct or 0.0),
        approved_leverage=0.0 if trigger_waiting else float(decision.leverage or 0.0),
        raw_projected_notional=0.0,
        approved_notional=0.0,
        approved_projected_notional=0.0,
        approved_qty=None,
        approved_quantity=None,
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )
    row = RiskCheck(
        symbol=decision.symbol,
        decision_run_id=kwargs.get("decision_run_id"),
        market_snapshot_id=kwargs.get("market_snapshot_id"),
        allowed=result.allowed,
        decision=decision.decision,
        reason_codes=list(result.reason_codes),
        approved_risk_pct=result.approved_risk_pct,
        approved_leverage=result.approved_leverage,
        payload=result.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    return result, row


def _entry_plan_fake_evaluate_risk_baseline_disagreement(session, settings_row, decision, market_snapshot, **kwargs):
    if decision.decision not in {"long", "short"}:
        return _entry_plan_fake_evaluate_risk(
            session,
            settings_row,
            decision,
            market_snapshot,
            **kwargs,
        )
    blocked_reason_codes = ["DETERMINISTIC_BASELINE_DISAGREEMENT"]
    return _entry_plan_fake_evaluate_risk_for_blockers(
        session,
        decision,
        blocked_reason_codes=blocked_reason_codes,
        **kwargs,
    )


def _entry_plan_fake_evaluate_risk_protection_unverified(session, settings_row, decision, market_snapshot, **kwargs):
    if decision.decision not in {"long", "short"}:
        return _entry_plan_fake_evaluate_risk(
            session,
            settings_row,
            decision,
            market_snapshot,
            **kwargs,
        )
    return _entry_plan_fake_evaluate_risk_for_blockers(
        session,
        decision,
        blocked_reason_codes=["DETERMINISTIC_BASELINE_DISAGREEMENT", "PROTECTION_STATE_UNVERIFIED"],
        **kwargs,
    )


def _entry_plan_fake_evaluate_risk_for_blockers(session, decision, *, blocked_reason_codes, **kwargs):
    result = RiskCheckResult(
        allowed=False,
        decision=decision.decision,
        reason_codes=blocked_reason_codes,
        blocked_reason_codes=blocked_reason_codes,
        adjustment_reason_codes=[],
        approved_risk_pct=0.0,
        approved_leverage=0.0,
        raw_projected_notional=100.0,
        approved_notional=0.0,
        approved_projected_notional=0.0,
        approved_qty=None,
        approved_quantity=None,
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )
    row = RiskCheck(
        symbol=decision.symbol,
        decision_run_id=kwargs.get("decision_run_id"),
        market_snapshot_id=kwargs.get("market_snapshot_id"),
        allowed=False,
        decision=decision.decision,
        reason_codes=list(result.reason_codes),
        approved_risk_pct=0.0,
        approved_leverage=0.0,
        payload=result.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    return result, row


def _entry_plan_fake_evaluate_risk_block_immediate(session, settings_row, decision, market_snapshot, **kwargs):
    if decision.entry_mode != "immediate":
        return _entry_plan_fake_evaluate_risk(
            session,
            settings_row,
            decision,
            market_snapshot,
            **kwargs,
        )
    blocked_reason_codes = ["MAX_LEVERAGE_EXCEEDED"]
    result = RiskCheckResult(
        allowed=False,
        decision=decision.decision,
        reason_codes=blocked_reason_codes,
        blocked_reason_codes=blocked_reason_codes,
        adjustment_reason_codes=[],
        approved_risk_pct=0.0,
        approved_leverage=0.0,
        raw_projected_notional=0.0,
        approved_notional=0.0,
        approved_projected_notional=0.0,
        approved_qty=None,
        approved_quantity=None,
        operating_mode="live",
        effective_leverage_cap=1.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )
    row = RiskCheck(
        symbol=decision.symbol,
        decision_run_id=kwargs.get("decision_run_id"),
        market_snapshot_id=kwargs.get("market_snapshot_id"),
        allowed=False,
        decision=decision.decision,
        reason_codes=list(result.reason_codes),
        approved_risk_pct=0.0,
        approved_leverage=0.0,
        payload=result.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    return result, row


def _patch_entry_plan_decision_flow(monkeypatch, orchestrator: TradingOrchestrator) -> None:
    def fake_agent_run(*args, **kwargs):
        return _entry_plan_test_decision(), "deterministic-mock", {}

    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_agent_run)
    monkeypatch.setattr("trading_mvp.services.orchestrator.evaluate_risk", _entry_plan_fake_evaluate_risk)


def _decision_funnel_events(db_session) -> list[AuditEvent]:
    return list(
        db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "decision_funnel_audit")
            .order_by(AuditEvent.id)
        )
    )


def _patch_entry_plan_recheck_dependencies(
    monkeypatch,
    settings_row,
    execute_live_trade_stub,
    risk_stub=_entry_plan_fake_evaluate_risk,
) -> None:
    monkeypatch.setattr("trading_mvp.services.orchestrator.get_openai_call_gate", lambda *args, **kwargs: _AllowAiGate())
    monkeypatch.setattr("trading_mvp.services.orchestrator.build_ai_prior_context", lambda *args, **kwargs: AIPriorContextPacket())
    monkeypatch.setattr("trading_mvp.services.orchestrator.evaluate_risk", risk_stub)
    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", execute_live_trade_stub)
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_exchange_sync_cycle",
        lambda self, **kwargs: {"status": "ok", "symbols": [settings_row.default_symbol]},
    )


def _entry_plan_zone_touch_snapshot() -> MarketSnapshotPayload:
    snapshot_time = utcnow_naive()
    return MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="1m",
        snapshot_time=snapshot_time,
        latest_price=69380.0,
        latest_volume=1400.0,
        candle_count=3,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=snapshot_time - timedelta(minutes=2),
                open=69120.0,
                high=69220.0,
                low=69020.0,
                close=69180.0,
                volume=1000.0,
            ),
            MarketCandle(
                timestamp=snapshot_time - timedelta(minutes=1),
                open=69180.0,
                high=69250.0,
                low=69080.0,
                close=69200.0,
                volume=1100.0,
            ),
            MarketCandle(
                timestamp=snapshot_time,
                open=69100.0,
                high=69420.0,
                low=68980.0,
                close=69380.0,
                volume=1400.0,
            ),
        ],
        derivatives_context=DerivativesContextPayload(source="binance_public", spread_bps=2.0),
    )


def _selection_candidate_row(
    *,
    symbol: str,
    decision: str,
    scenario: str,
    total_score: float,
    priority: bool = False,
    trend_alignment: str = "bullish_aligned",
    primary_regime: str = "trend",
    weak_volume: bool = False,
    momentum_weakening: bool = False,
    returns: list[float] | None = None,
    scenario_signature: str | None = None,
    performance_summary: dict[str, object] | None = None,
    recent_signal_performance: float | None = None,
    derivatives_alignment: float = 0.62,
    lead_lag_alignment: float = 0.64,
    slippage_sensitivity: float = 0.6,
    confidence_consistency: float = 0.6,
) -> dict[str, object]:
    candidate = TradeDecisionCandidate(
        candidate_id=f"{symbol}:15m:{scenario}",
        scenario=scenario,  # type: ignore[arg-type]
        decision=decision,  # type: ignore[arg-type]
        symbol=symbol,
        timeframe="15m",
        confidence=0.7,
        entry_zone_min=100.0 if decision in {"long", "short"} else None,
        entry_zone_max=101.0 if decision in {"long", "short"} else None,
        stop_loss=99.0 if decision == "long" else (102.0 if decision == "short" else None),
        take_profit=103.0 if decision == "long" else (98.0 if decision == "short" else None),
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=1.0,
        rationale_codes=["TEST_SELECTION"],
        explanation_short="selection test candidate",
        explanation_detailed="Selection test candidate used for portfolio rotation ranking.",
    )
    return {
        "symbol": symbol,
        "priority": priority,
        "candidate": candidate,
        "score": TradeDecisionCandidateScore(
            total_score=total_score,
            recent_signal_performance=recent_signal_performance if recent_signal_performance is not None else total_score,
            derivatives_alignment=derivatives_alignment,
            lead_lag_alignment=lead_lag_alignment,
            slippage_sensitivity=slippage_sensitivity,
            confidence_consistency=confidence_consistency,
        ),
        "feature_payload": None,
        "regime_summary": {
            "primary_regime": primary_regime,
            "trend_alignment": trend_alignment,
            "weak_volume": weak_volume,
            "momentum_weakening": momentum_weakening,
        },
        "performance_summary": performance_summary
        or {
            "score": total_score,
            "sample_size": 0,
            "hit_rate": 0.0,
            "expectancy": 0.0,
            "net_pnl_after_fees": 0.0,
            "avg_signed_slippage_bps": 0.0,
            "loss_streak": 0,
            "underperforming": False,
            "components": {},
        },
        "scenario_signature": scenario_signature or f"{decision}:{scenario}:{primary_regime}:{trend_alignment}",
        "returns": list(returns or []),
        "market_snapshot": None,
    }


def _ranking_candidate_payload(
    *,
    symbol: str = "BTCUSDT",
    strategy_engine: str = "trend_pullback_engine",
    decision: str = "long",
    scenario: str = "trend_follow",
    holding_profile: str = "scalp",
) -> dict[str, object]:
    candidate = TradeDecisionCandidate(
        candidate_id=f"{symbol}:15m:{scenario}:{strategy_engine}",
        scenario=scenario,  # type: ignore[arg-type]
        decision=decision,  # type: ignore[arg-type]
        symbol=symbol,
        timeframe="15m",
        confidence=0.72,
        entry_zone_min=100.0 if decision in {"long", "short"} else None,
        entry_zone_max=101.0 if decision in {"long", "short"} else None,
        stop_loss=99.0 if decision == "long" else (102.0 if decision == "short" else None),
        take_profit=103.0 if decision == "long" else (98.0 if decision == "short" else None),
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=1.0,
        rationale_codes=["TEST_SELECTION"],
        holding_profile=holding_profile,  # type: ignore[arg-type]
        holding_profile_reason="test_holding_profile",
        strategy_engine=strategy_engine,
        strategy_engine_context={"engine_name": strategy_engine},
        explanation_short="ranking candidate",
        explanation_detailed="Ranking candidate payload used for scheduler trigger tests.",
    )
    return {
        "symbol": symbol,
        "selected": True,
        "selected_reason": "ranked_portfolio_focus",
        "selection_reason": "ranked_portfolio_focus",
        "rejected_reason": None,
        "entry_mode": "breakout_confirm" if strategy_engine == "breakout_exception_engine" else "pullback_confirm",
        "strategy_engine": strategy_engine,
        "strategy_engine_context": {"engine_name": strategy_engine},
        "holding_profile": holding_profile,
        "holding_profile_reason": "test_holding_profile",
        "holding_profile_context": {
            "holding_profile": holding_profile,
            "holding_profile_reason": "test_holding_profile",
            "cadence_hint": {"decision_interval_minutes": 15},
        },
        "assigned_slot": "slot_1",
        "slot_label": "high_conviction",
        "slot_reason": "high_conviction_slot",
        "slot_conviction_score": 0.83,
        "meta_gate_probability": 0.74,
        "agreement_alignment_score": 0.79,
        "agreement_level_hint": "full_agreement_likely",
        "execution_quality_score": 0.77,
        "slot_risk_pct_multiplier": 1.0,
        "slot_leverage_multiplier": 1.0,
        "slot_notional_multiplier": 1.0,
        "slot_applies_soft_cap": False,
        "portfolio_weight": 0.64,
        "candidate_weight": 0.64,
        "breadth_score_multiplier": 1.04,
        "breadth_score_adjustment": 0.04,
        "breadth_hold_bias": 0.95,
        "breadth_adjustment_reasons": ["breadth_trend_alignment_boost"],
        "candidate": candidate.model_dump(mode="json"),
        "score": {"total_score": 0.71},
        "performance_summary": {"score": 0.71, "sample_size": 1, "underperforming": False},
    }


def _interval_review_plan(
    *,
    symbol: str = "BTCUSDT",
    trigger_reason: str | None,
    trigger_deduped: bool = False,
    selection_context: dict[str, object] | None = None,
    dedupe_reason: str | None = None,
    fingerprint_changed_fields: list[str] | None = None,
    forced_review_reason: str | None = None,
    applied_review_cadence_minutes: int | None = 15,
    review_cadence_source: str | None = "holding_profile_cadence_hint",
    holding_profile_cadence_hint: dict[str, object] | None = None,
    cadence_fallback_reason: str | None = None,
    max_review_age_minutes: int | None = 45,
) -> dict[str, object]:
    now = utcnow_naive()
    trigger = (
        {
            "trigger_reason": trigger_reason,
            "symbol": symbol,
            "timeframe": "15m",
            "strategy_engine": "trend_pullback_engine",
            "holding_profile": "scalp",
            "assigned_slot": "slot_1",
            "candidate_weight": 0.64,
            "reason_codes": ["TEST_SELECTION"],
            "trigger_fingerprint": "fingerprint-1234",
            "fingerprint_basis": {"position_state_bucket": "flat"},
            "fingerprint_changed_fields": list(fingerprint_changed_fields or []),
            "last_decision_at": None,
            "last_material_review_at": now.isoformat(),
            "forced_review_reason": forced_review_reason,
            "applied_review_cadence_minutes": applied_review_cadence_minutes,
            "review_cadence_source": review_cadence_source,
            "holding_profile_cadence_hint": dict(
                holding_profile_cadence_hint
                or {"holding_profile": "scalp", "decision_interval_minutes": 15}
            ),
            "cadence_fallback_reason": cadence_fallback_reason,
            "max_review_age_minutes": max_review_age_minutes,
            "cadence_profile_summary": {
                "mode": "watch",
                "active_holding_profile": "scalp",
            },
            "triggered_at": now.isoformat(),
        }
        if trigger_reason is not None
        else None
    )
    return {
        "generated_at": now.isoformat(),
        "candidate_selection": {"rankings": []},
        "plans": [
            {
                "symbol": symbol,
                "timeframe": "15m",
                "cadence": {
                    "mode": "watch",
                    "effective_cadence": {
                        "decision_cycle_interval_minutes": 15,
                        "ai_call_interval_minutes": 15,
                    },
                },
                "selection_context": selection_context or {},
                "trigger": trigger,
                "trigger_deduped": trigger_deduped,
                "last_decision_at": None,
                "last_ai_invoked_at": None,
                "last_material_review_at": now.isoformat(),
                "next_ai_review_due_at": (now + timedelta(minutes=15)).isoformat(),
                "applied_review_cadence_minutes": applied_review_cadence_minutes,
                "review_cadence_source": review_cadence_source,
                "holding_profile_cadence_hint": dict(
                    holding_profile_cadence_hint
                    or {"holding_profile": "scalp", "decision_interval_minutes": 15}
                ),
                "cadence_fallback_reason": cadence_fallback_reason,
                "max_review_age_minutes": max_review_age_minutes,
                "cadence_profile_summary": {
                    "mode": "watch",
                    "active_holding_profile": "scalp",
                },
                "fingerprint_changed_fields": list(fingerprint_changed_fields or []),
                "dedupe_reason": dedupe_reason,
                "forced_review_reason": forced_review_reason,
                "last_ai_skip_reason": "TRIGGER_DEDUPED" if trigger_deduped else "NO_EVENT" if trigger is None else None,
            }
        ],
    }


def _interval_budget_review_plan(
    symbols: list[str],
    *,
    breadth_regime: str = "mixed",
    protection_symbol: str | None = None,
    transition_watch_symbol: str | None = None,
) -> dict[str, object]:
    now = utcnow_naive()
    plans: list[dict[str, object]] = []
    rankings: list[dict[str, object]] = []
    for index, symbol in enumerate(symbols):
        candidate_weight = round(0.9 - (index * 0.1), 6)
        score_total = round(0.8 - (index * 0.05), 6)
        trigger_reason = (
            "protection_review_event"
            if protection_symbol is not None and symbol == protection_symbol
            else "entry_candidate_event"
        )
        reason_codes = ["TEST_SELECTION"]
        ai_call_policy = None
        if transition_watch_symbol is not None and symbol == transition_watch_symbol:
            reason_codes.append("SOFT_SIGNAL_TRANSITION_WATCH")
            ai_call_policy = {
                "soft_signal_review_mode": "transition_watch",
                "ai_call_allowed": True,
            }
        selection_context = {
            "assigned_slot": f"slot_{index + 1}",
            "candidate_weight": candidate_weight,
            "slot_conviction_score": round(0.75 - (index * 0.04), 6),
            "meta_gate_probability": round(0.7 - (index * 0.03), 6),
            "score": {"total_score": score_total},
            "selected": True,
        }
        if ai_call_policy is not None:
            selection_context["ai_call_policy"] = ai_call_policy
        rankings.append(
            {
                "symbol": symbol,
                "selected": True,
                "candidate_weight": candidate_weight,
                "score": {"total_score": score_total},
            }
        )
        plans.append(
            {
                "symbol": symbol,
                "timeframe": "15m",
                "cadence": {
                    "mode": "watch",
                    "effective_cadence": {
                        "decision_cycle_interval_minutes": 15,
                        "ai_call_interval_minutes": 15,
                    },
                },
                "selection_context": selection_context,
                "trigger": {
                    "trigger_reason": trigger_reason,
                    "symbol": symbol,
                    "timeframe": "15m",
                    "strategy_engine": "trend_pullback_engine",
                    "holding_profile": "scalp",
                    "assigned_slot": f"slot_{index + 1}",
                    "candidate_weight": candidate_weight,
                    "reason_codes": reason_codes,
                    "trigger_fingerprint": f"fingerprint-{symbol}",
                    "fingerprint_basis": {"position_state_bucket": "flat"},
                    "fingerprint_changed_fields": [],
                    "last_decision_at": None,
                    "last_material_review_at": now.isoformat(),
                    "forced_review_reason": None,
                    "applied_review_cadence_minutes": 15,
                    "review_cadence_source": "holding_profile_cadence_hint",
                    "holding_profile_cadence_hint": {
                        "holding_profile": "scalp",
                        "decision_interval_minutes": 15,
                    },
                    "cadence_fallback_reason": None,
                    "max_review_age_minutes": 45,
                    "cadence_profile_summary": {
                        "mode": "watch",
                        "active_holding_profile": "scalp",
                    },
                    "triggered_at": now.isoformat(),
                },
                "trigger_deduped": False,
                "last_decision_at": None,
                "last_ai_invoked_at": None,
                "last_material_review_at": now.isoformat(),
                "next_ai_review_due_at": (now + timedelta(minutes=15)).isoformat(),
                "applied_review_cadence_minutes": 15,
                "review_cadence_source": "holding_profile_cadence_hint",
                "holding_profile_cadence_hint": {
                    "holding_profile": "scalp",
                    "decision_interval_minutes": 15,
                },
                "cadence_fallback_reason": None,
                "max_review_age_minutes": 45,
                "cadence_profile_summary": {
                    "mode": "watch",
                    "active_holding_profile": "scalp",
                },
                "fingerprint_changed_fields": [],
                "dedupe_reason": None,
                "forced_review_reason": None,
                "last_ai_skip_reason": None,
                "ai_call_policy": ai_call_policy,
            }
        )
    return {
        "generated_at": now.isoformat(),
        "candidate_selection": {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": breadth_regime,
            "breadth_summary": {"breadth_regime": breadth_regime},
            "capacity_reason": f"{breadth_regime}_test_capacity",
            "selected_symbols": list(symbols),
            "skipped_symbols": [],
            "rankings": rankings,
        },
        "plans": plans,
    }


def _seed_setup_cluster_history(
    db_session,
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    decision: str = "long",
    scenario_rationale_codes: list[str] | None = None,
    entry_mode: str = "pullback_confirm",
    primary_regime: str = "bullish",
    trend_alignment: str = "bullish_aligned",
    net_pnls: list[float],
    signed_slippage_bps: list[float] | None = None,
    start_offset_minutes: int = 5,
) -> None:
    now = utcnow_naive()
    rationale_codes = scenario_rationale_codes or ["PULLBACK_ENTRY_BIAS", "BULLISH_CONTINUATION_PULLBACK"]
    slippage_values = signed_slippage_bps or [14.0 for _ in net_pnls]
    for index, net_pnl in enumerate(net_pnls):
        created_at = now - timedelta(minutes=start_offset_minutes + (index * 5))
        decision_row = AgentRun(
            role="trading_decision",
            trigger_event="interval_decision_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="deterministic-mock",
            summary="setup cluster history",
            input_payload={
                "features": {
                    "regime": {
                        "primary_regime": primary_regime,
                        "trend_alignment": trend_alignment,
                    }
                }
            },
            output_payload={
                "symbol": symbol,
                "timeframe": timeframe,
                "decision": decision,
                "entry_mode": entry_mode,
                "rationale_codes": rationale_codes,
                "confidence": 0.62,
                "risk_pct": 0.01,
                "leverage": 2.0,
            },
            metadata_json={},
            schema_valid=True,
            started_at=created_at,
            completed_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
        db_session.add(decision_row)
        db_session.flush()
        risk_row = RiskCheck(
            symbol=symbol,
            decision_run_id=decision_row.id,
            market_snapshot_id=None,
            allowed=True,
            decision=decision,
            reason_codes=[],
            approved_risk_pct=0.01,
            approved_leverage=2.0,
            payload={"allowed": True},
            created_at=created_at,
            updated_at=created_at,
        )
        db_session.add(risk_row)
        db_session.flush()
        order_row = Order(
            symbol=symbol,
            decision_run_id=decision_row.id,
            risk_check_id=risk_row.id,
            position_id=None,
            side="buy" if decision == "long" else "sell",
            order_type="market",
            mode="live",
            status="filled",
            requested_quantity=0.01,
            requested_price=100.0,
            filled_quantity=0.01,
            average_fill_price=100.0,
            reason_codes=[],
            metadata_json={},
            created_at=created_at,
            updated_at=created_at,
        )
        db_session.add(order_row)
        db_session.flush()
        execution_row = Execution(
            order_id=order_row.id,
            position_id=None,
            symbol=symbol,
            status="filled",
            external_trade_id=f"cluster-{decision_row.id}",
            fill_price=100.0,
            fill_quantity=0.01,
            fee_paid=1.0,
            commission_asset="USDT",
            slippage_pct=abs(float(slippage_values[index])) / 10000.0,
            realized_pnl=float(net_pnl) + 1.0,
            payload={
                "signed_slippage_bps": float(slippage_values[index]),
                "signed_slippage_pct": float(slippage_values[index]) / 10000.0,
            },
            created_at=created_at,
            updated_at=created_at,
        )
        db_session.add(execution_row)
    db_session.flush()


def test_seed_bootstraps_without_demo_trading_data(db_session) -> None:
    output = seed_demo_data(db_session)
    assert output["status"] == "bootstrapped"
    assert db_session.scalar(select(User).limit(1)) is not None
    assert db_session.scalar(select(AgentRun).limit(1)) is None


def test_ai_disabled_collects_market_data_only(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = False
    settings_row.binance_market_data_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=69900.0,
                high=70100.0,
                low=69800.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )

    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda **kwargs: {"15m": snapshot, "1h": snapshot.model_copy(update={"timeframe": "1h"}), "4h": snapshot.model_copy(update={"timeframe": "4h"})},
    )

    result = TradingOrchestrator(db_session).run_selected_symbols_cycle(trigger_event="realtime_cycle")
    db_session.commit()

    assert result["mode"] == "market_data_only"
    assert result["results"][0]["status"] == "market_data_only"
    assert db_session.scalar(select(MarketSnapshot).limit(1)) is not None
    assert db_session.scalar(select(FeatureSnapshot).limit(1)) is None
    assert db_session.scalar(select(AgentRun).limit(1)) is None
    assert db_session.scalar(select(RiskCheck).limit(1)) is None
    latest_pnl = db_session.scalar(select(PnLSnapshot).limit(1))
    if latest_pnl is not None:
        assert latest_pnl.daily_pnl == 0.0
        assert latest_pnl.cumulative_pnl == 0.0
        assert latest_pnl.consecutive_losses == 0
    audit_event = db_session.scalar(select(AuditEvent).limit(1))
    assert audit_event is not None
    assert audit_event.event_type == "decision_funnel_audit"
    assert audit_event.payload["stage"] == "ai_disabled"
    assert audit_event.payload["status"] == "market_data_only"


def test_ai_disabled_hourly_window_creates_only_market_snapshots(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = False
    settings_row.binance_market_data_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=69900.0,
                high=70100.0,
                low=69800.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )

    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda **kwargs: {"15m": snapshot, "1h": snapshot.model_copy(update={"timeframe": "1h"}), "4h": snapshot.model_copy(update={"timeframe": "4h"})},
    )

    result = run_window(db_session, "1h", triggered_by="scheduler")
    db_session.commit()

    assert result["status"] == "market_data_only"
    assert db_session.scalar(select(MarketSnapshot).limit(1)) is not None
    assert db_session.scalar(select(FeatureSnapshot).limit(1)) is not None
    assert db_session.scalar(select(AgentRun).limit(1)) is None
    assert db_session.scalar(select(RiskCheck).limit(1)) is None


def test_ai_enabled_hourly_window_only_refreshes_market_data(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.binance_market_data_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=69900.0,
                high=70100.0,
                low=69800.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )

    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda **kwargs: {"15m": snapshot, "1h": snapshot.model_copy(update={"timeframe": "1h"}), "4h": snapshot.model_copy(update={"timeframe": "4h"})},
    )

    result = run_window(db_session, "1h", triggered_by="scheduler")
    db_session.commit()

    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))
    assert result["window"] == "1h"
    assert result["outcome"]["mode"] == "market_refresh"
    assert scheduler_run is not None
    assert scheduler_run.workflow == "market_refresh_cycle"
    assert db_session.scalar(select(MarketSnapshot).limit(1)) is not None
    assert db_session.scalar(select(FeatureSnapshot).limit(1)) is not None
    assert db_session.scalar(select(AgentRun).limit(1)) is None
    assert db_session.scalar(select(RiskCheck).limit(1)) is None


def test_out_of_scope_review_windows_are_disabled(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    result = run_window(db_session, "24h", triggered_by="scheduler")
    db_session.commit()

    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))
    assert scheduler_run is None
    assert result["window"] == "24h"
    assert result["status"] == "disabled"
    assert result["reason"] == "REVIEW_WINDOW_DISABLED_OUT_OF_SCOPE"


def test_run_due_windows_noops_for_disabled_aux_workflows(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.schedule_windows = ["1h", "4h", "12h", "24h"]
    db_session.add(settings_row)
    db_session.flush()

    result = run_due_windows(db_session)

    assert result == []
    assert db_session.scalar(select(SchedulerRun).limit(1)) is None


def test_pipeline_creates_risk_and_execution_records(monkeypatch, db_session) -> None:
    settings_row = _prepare_live_entry_plan_settings(db_session)

    class EnabledSettings:
        live_trading_env_enabled = True

    monkeypatch.setattr("trading_mvp.services.risk.get_settings", lambda: EnabledSettings())
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_exchange_sync_cycle",
        lambda self, **kwargs: {"status": "ok", "symbols": [settings_row.default_symbol]},
    )

    def fake_execute_live_trade(
        session,
        settings_row,
        decision_run_id,
        decision,
        market_snapshot,
        risk_result,
        risk_row=None,
        cycle_id=None,
        snapshot_id=None,
        idempotency_key=None,
    ):
        raise AssertionError("new entry decision should arm a pending plan instead of executing immediately")

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fake_execute_live_trade)

    orchestrator = TradingOrchestrator(db_session)
    _patch_entry_plan_decision_flow(monkeypatch, orchestrator)
    result = orchestrator.run_decision_cycle(trigger_event="manual", upto_index=140)
    db_session.commit()
    decision_run = db_session.get(AgentRun, result["decision_run_id"])

    assert result["decision"]["decision"] == "long"
    assert result["risk_result"]["allowed"] is False
    assert "ENTRY_TRIGGER_NOT_MET" in result["risk_result"]["blocked_reason_codes"]
    assert result["execution"] is None
    assert result["status"] == "entry_plan_armed"
    assert result["entry_plan"] is not None
    assert result["entry_plan"]["plan_status"] == "armed"
    assert result["decision_reference"]["market_snapshot_id"] == result["market_snapshot_id"]
    assert result["decision_reference"]["market_snapshot_source"] == "seed_fallback"
    assert result["decision_reference"]["market_snapshot_stale"] is False
    assert result["decision_reference"]["freshness_blocking"] is False
    assert result["decision_reference"]["account_sync_at"] is None
    assert result["decision_reference"]["account_sync_status"] == "unknown"
    assert result["decision_reference"]["sync_freshness_summary"]["account"]["stale"] is False
    assert result["decision_reference"]["positions_sync_at"] is not None
    assert decision_run is not None
    assert decision_run.input_payload["decision_reference"]["market_snapshot_id"] == result["market_snapshot_id"]
    assert decision_run.input_payload["decision_reference"]["sync_freshness_summary"]["account"]["stale"] is False
    pending_plan = db_session.scalar(select(PendingEntryPlan).limit(1))
    assert pending_plan is not None
    assert pending_plan.plan_status == "armed"
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None
    assert db_session.scalar(select(RiskCheck).limit(1)) is not None
    assert db_session.scalar(select(AuditEvent).limit(1)) is not None


def test_decision_funnel_audit_records_entry_waiting_plan(monkeypatch, db_session) -> None:
    settings_row = _prepare_live_entry_plan_settings(db_session)
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_exchange_sync_cycle",
        lambda self, **kwargs: {"status": "ok", "symbols": [settings_row.default_symbol]},
    )

    def fail_execute(*args, **kwargs):
        raise AssertionError("entry waiting plan must not submit immediately")

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)
    orchestrator = TradingOrchestrator(db_session)
    _patch_entry_plan_decision_flow(monkeypatch, orchestrator)

    result = orchestrator.run_decision_cycle(
        trigger_event="manual",
        upto_index=140,
        exchange_sync_checked=True,
        auto_resume_checked=True,
    )
    db_session.flush()

    funnel_events = _decision_funnel_events(db_session)
    assert result["status"] == "entry_plan_armed"
    assert any(event.payload["entry_plan_status"] == "armed" for event in funnel_events)
    summary_event = funnel_events[-1]
    assert summary_event.payload["status"] == "entry_plan_armed"
    assert summary_event.payload["blocked_reason"] == "ENTRY_TRIGGER_NOT_MET"
    assert summary_event.payload["order_status"] == "not_submitted"


def test_decision_funnel_audit_records_no_candidate(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["NOTINBOOK"], timeframe="15m")
    db_session.flush()

    event = _decision_funnel_events(db_session)[-1]
    assert event.payload["cycle_id"].startswith("decision-plan:all:")
    assert event.payload["status"] == "no_candidate"
    assert event.payload["entry_plan_status"] == "not_created"
    assert event.payload["blocked_reason"] == "no_eligible_symbol"
    assert event.payload["order_status"] == "not_submitted"


def test_decision_funnel_audit_records_ai_hold_reason(monkeypatch, db_session) -> None:
    settings_row = _prepare_live_entry_plan_settings(db_session)
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_exchange_sync_cycle",
        lambda self, **kwargs: {"status": "ok", "symbols": [settings_row.default_symbol]},
    )

    def fake_agent_run(*args, **kwargs):
        return (
            _entry_plan_test_decision().model_copy(
                update={
                    "decision": "hold",
                    "entry_mode": "none",
                    "entry_zone_min": None,
                    "entry_zone_max": None,
                    "invalidation_price": None,
                    "max_chase_bps": None,
                    "idea_ttl_minutes": None,
                    "stop_loss": None,
                    "take_profit": None,
                    "rationale_codes": ["NO_VALID_ENTRY_SETUP"],
                    "no_trade_reason_codes": ["NO_VALID_ENTRY_SETUP"],
                    "explanation_short": "No valid entry setup.",
                }
            ),
            "openai",
            {"source": "llm"},
        )

    def fake_evaluate_risk(session, settings_row, decision, market_snapshot, **kwargs):
        result = RiskCheckResult(
            allowed=False,
            decision="hold",
            reason_codes=["HOLD_DECISION"],
            blocked_reason_codes=["HOLD_DECISION"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            operating_mode="hold",
            effective_leverage_cap=5.0,
            symbol_risk_tier="btc",
            exposure_metrics={},
        )
        row = RiskCheck(
            symbol=decision.symbol,
            decision_run_id=kwargs.get("decision_run_id"),
            market_snapshot_id=kwargs.get("market_snapshot_id"),
            allowed=False,
            decision="hold",
            reason_codes=list(result.reason_codes),
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload=result.model_dump(mode="json"),
        )
        session.add(row)
        session.flush()
        return result, row

    def fail_execute(*args, **kwargs):
        raise AssertionError("hold decision must not submit an order")

    orchestrator = TradingOrchestrator(db_session)
    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_agent_run)
    monkeypatch.setattr("trading_mvp.services.orchestrator.evaluate_risk", fake_evaluate_risk)
    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    result = orchestrator.run_decision_cycle(
        trigger_event="manual",
        upto_index=140,
        exchange_sync_checked=True,
        auto_resume_checked=True,
    )
    db_session.flush()

    event = _decision_funnel_events(db_session)[-1]
    assert result["decision"]["decision"] == "hold"
    assert event.payload["status"] == "hold"
    assert event.payload["hold_reason"] == "NO_VALID_ENTRY_SETUP"
    assert "HOLD_DECISION" in event.payload["hold_reason_codes"]
    assert event.payload["order_status"] == "not_submitted"


def test_decision_funnel_audit_records_risk_blocked_reason(monkeypatch, db_session) -> None:
    settings_row = _prepare_live_entry_plan_settings(db_session)
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_exchange_sync_cycle",
        lambda self, **kwargs: {"status": "ok", "symbols": [settings_row.default_symbol]},
    )

    def fake_agent_run(*args, **kwargs):
        return (
            _entry_plan_test_decision().model_copy(update={"entry_mode": "immediate"}),
            "openai",
            {"source": "llm"},
        )

    def fail_execute(*args, **kwargs):
        raise AssertionError("risk-blocked decision must not submit an order")

    orchestrator = TradingOrchestrator(db_session)
    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_agent_run)
    monkeypatch.setattr("trading_mvp.services.orchestrator.evaluate_risk", _entry_plan_fake_evaluate_risk_block_immediate)
    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    result = orchestrator.run_decision_cycle(
        trigger_event="manual",
        upto_index=140,
        exchange_sync_checked=True,
        auto_resume_checked=True,
    )
    db_session.flush()

    event = _decision_funnel_events(db_session)[-1]
    assert result["risk_result"]["allowed"] is False
    assert event.payload["status"] == "risk_blocked"
    assert event.payload["final_risk_status"] == "blocked"
    assert event.payload["blocked_reason"] == "MAX_LEVERAGE_EXCEEDED"
    assert event.payload["entry_plan_status"] == "not_created"


def test_entry_plan_watcher_ai_rechecks_zone_touch_before_execution(monkeypatch, db_session) -> None:
    settings_row = _prepare_live_entry_plan_settings(db_session)

    ai_calls: list[dict[str, object]] = []

    def fake_agent_run(_market_snapshot, _feature_payload, _open_positions, _risk_context, *, use_ai, **kwargs):
        ai_context = kwargs.get("ai_context")
        ai_calls.append(
            {
                "use_ai": use_ai,
                "trigger_type": getattr(ai_context, "trigger_type", None),
            }
        )
        return _entry_plan_test_decision(), "openai", {"source": "llm"}

    executions: list[dict[str, object]] = []

    def fake_execute_live_trade(
        session,
        settings_row,
        decision_run_id,
        decision,
        market_snapshot,
        risk_result,
        risk_row=None,
        cycle_id=None,
        snapshot_id=None,
        idempotency_key=None,
    ):
        executions.append(
            {
                "decision_run_id": decision_run_id,
                "entry_mode": decision.entry_mode,
                "entry_zone_min": decision.entry_zone_min,
                "entry_zone_max": decision.entry_zone_max,
                "rationale_codes": list(decision.rationale_codes),
                "risk_allowed": risk_result.allowed,
            }
        )
        return {"status": "filled", "order_id": 123, "client_order_id": idempotency_key}

    _patch_entry_plan_recheck_dependencies(monkeypatch, settings_row, fake_execute_live_trade)

    orchestrator = TradingOrchestrator(db_session)
    monkeypatch.setattr(orchestrator, "_entry_plan_control_block", lambda operational_status: (False, []))
    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_agent_run)

    arm_result = orchestrator.run_decision_cycle(
        trigger_event="manual",
        upto_index=140,
        exchange_sync_checked=True,
        auto_resume_checked=True,
    )
    db_session.commit()
    watch_result = orchestrator.run_entry_plan_watcher_cycle(
        symbols=["BTCUSDT"],
        exchange_sync_checked=True,
        auto_resume_checked=True,
        market_snapshot_override=_entry_plan_zone_touch_snapshot(),
    )
    db_session.flush()

    plan_result = watch_result["results"][0]["plans"][0]
    recheck_run_id = plan_result["ai_recheck"]["decision_run_id"]

    assert arm_result["status"] == "entry_plan_armed"
    assert len(ai_calls) == 2
    assert ai_calls[1]["use_ai"] is True
    assert ai_calls[1]["trigger_type"] == "entry_candidate_event"
    assert "PENDING_ENTRY_PLAN_RECHECK" in plan_result["ai_recheck"]["trigger"]["reason_codes"]
    assert plan_result["status"] == "triggered"
    assert executions[0]["decision_run_id"] == recheck_run_id
    assert executions[0]["entry_mode"] == "immediate"
    assert executions[0]["entry_zone_min"] == 69380.0
    assert executions[0]["entry_zone_max"] == 69380.0
    assert executions[0]["risk_allowed"] is True
    assert "PENDING_ENTRY_PLAN_RECHECK_CONFIRMED" in executions[0]["rationale_codes"]
    pending_plan = db_session.scalar(select(PendingEntryPlan).limit(1))
    assert pending_plan is not None
    assert pending_plan.plan_status == "triggered"
    assert pending_plan.metadata_json["last_ai_recheck_decision_run_id"] == recheck_run_id
    assert pending_plan.metadata_json["last_ai_recheck_decision"] == "long"
    risk_approved = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "decision_risk_approved",
            AuditEvent.entity_id == str(recheck_run_id),
        )
    )
    assert risk_approved is not None
    assert risk_approved.payload["ai_call_event"] == "AI_DECISION_APPROVED_BY_RISK"
    funnel_event = next(
        event
        for event in reversed(_decision_funnel_events(db_session))
        if event.payload["stage"] == "entry_plan_triggered"
    )
    assert funnel_event.payload["entry_plan_status"] == "triggered"
    assert funnel_event.payload["m1_confirmation_status"] == "passed"
    assert funnel_event.payload["final_risk_status"] == "approved"
    assert funnel_event.payload["order_status"] == "filled"


def test_entry_plan_watcher_cancels_when_ai_recheck_holds(monkeypatch, db_session) -> None:
    settings_row = _prepare_live_entry_plan_settings(db_session)

    decisions = [
        _entry_plan_test_decision(),
        _entry_plan_test_decision().model_copy(
            update={
                "decision": "hold",
                "entry_mode": "none",
                "entry_zone_min": None,
                "entry_zone_max": None,
                "invalidation_price": None,
                "max_chase_bps": None,
                "idea_ttl_minutes": None,
                "stop_loss": None,
                "take_profit": None,
                "rationale_codes": ["PLAN_RECHECK_NO_LONGER_VALID"],
            }
        ),
    ]

    def fake_agent_run(*args, **kwargs):
        return decisions.pop(0), "openai", {"source": "llm"}

    def fail_execute(*args, **kwargs):
        raise AssertionError("AI hold recheck must not execute the pending entry plan")

    _patch_entry_plan_recheck_dependencies(monkeypatch, settings_row, fail_execute)

    orchestrator = TradingOrchestrator(db_session)
    monkeypatch.setattr(orchestrator, "_entry_plan_control_block", lambda operational_status: (False, []))
    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_agent_run)

    orchestrator.run_decision_cycle(
        trigger_event="manual",
        upto_index=140,
        exchange_sync_checked=True,
        auto_resume_checked=True,
    )
    db_session.commit()
    watch_result = orchestrator.run_entry_plan_watcher_cycle(
        symbols=["BTCUSDT"],
        exchange_sync_checked=True,
        auto_resume_checked=True,
        market_snapshot_override=_entry_plan_zone_touch_snapshot(),
    )
    db_session.flush()

    plan_result = watch_result["results"][0]["plans"][0]
    pending_plan = db_session.scalar(select(PendingEntryPlan).limit(1))
    canceled = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "pending_entry_plan_canceled")
    )

    assert plan_result["status"] == "canceled"
    assert plan_result["blocked_reasons"] == ["PLAN_RECHECK_REJECTED"]
    assert plan_result["ai_recheck"]["decision"]["decision"] == "hold"
    assert pending_plan is not None
    assert pending_plan.plan_status == "canceled"
    assert pending_plan.canceled_reason == "PLAN_RECHECK_REJECTED"
    assert canceled is not None
    assert canceled.payload["reason"] == "PLAN_RECHECK_REJECTED"
    assert canceled.payload["detail"]["recheck_decision"] == "hold"
    assert db_session.scalar(select(Order).limit(1)) is None


def test_entry_plan_watcher_does_not_execute_when_recheck_risk_blocks(monkeypatch, db_session) -> None:
    settings_row = _prepare_live_entry_plan_settings(db_session)
    decisions = [_entry_plan_test_decision(), _entry_plan_test_decision()]

    def fake_agent_run(*args, **kwargs):
        return decisions.pop(0), "openai", {"source": "llm"}

    def fail_execute(*args, **kwargs):
        raise AssertionError("risk-blocked AI recheck must not submit execution")

    _patch_entry_plan_recheck_dependencies(
        monkeypatch,
        settings_row,
        fail_execute,
        risk_stub=_entry_plan_fake_evaluate_risk_block_immediate,
    )

    orchestrator = TradingOrchestrator(db_session)
    monkeypatch.setattr(orchestrator, "_entry_plan_control_block", lambda operational_status: (False, []))
    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_agent_run)

    orchestrator.run_decision_cycle(
        trigger_event="manual",
        upto_index=140,
        exchange_sync_checked=True,
        auto_resume_checked=True,
    )
    db_session.commit()
    watch_result = orchestrator.run_entry_plan_watcher_cycle(
        symbols=["BTCUSDT"],
        exchange_sync_checked=True,
        auto_resume_checked=True,
        market_snapshot_override=_entry_plan_zone_touch_snapshot(),
    )
    db_session.flush()

    plan_result = watch_result["results"][0]["plans"][0]
    recheck_run_id = plan_result["ai_recheck"]["decision_run_id"]
    pending_plan = db_session.scalar(select(PendingEntryPlan).limit(1))
    risk_blocked = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "decision_risk_blocked",
            AuditEvent.entity_id == str(recheck_run_id),
        )
    )

    assert plan_result["status"] == "risk_blocked"
    assert plan_result["execution"] is None
    assert plan_result["risk_result"]["allowed"] is False
    assert "MAX_LEVERAGE_EXCEEDED" in plan_result["risk_result"]["blocked_reason_codes"]
    assert pending_plan is not None
    assert pending_plan.plan_status == "armed"
    assert pending_plan.metadata_json["last_ai_recheck_risk_blocked_decision_run_id"] == recheck_run_id
    assert risk_blocked is not None
    assert risk_blocked.payload["ai_call_event"] == "AI_DECISION_BLOCKED_BY_RISK"
    funnel_event = next(
        event
        for event in reversed(_decision_funnel_events(db_session))
        if event.payload["stage"] == "final_risk_guard"
    )
    assert funnel_event.payload["status"] == "final_risk_blocked"
    assert funnel_event.payload["final_risk_status"] == "blocked"
    assert funnel_event.payload["blocked_reason"] == "MAX_LEVERAGE_EXCEEDED"
    assert funnel_event.payload["order_status"] == "not_submitted"
    assert db_session.scalar(select(Order).limit(1)) is None


def test_historical_replay_never_executes_live(monkeypatch, db_session) -> None:
    settings_row = _prepare_live_entry_plan_settings(db_session)

    class EnabledSettings:
        live_trading_env_enabled = True

    monkeypatch.setattr("trading_mvp.services.risk.get_settings", lambda: EnabledSettings())
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_exchange_sync_cycle",
        lambda self, **kwargs: {"status": "ok", "symbols": [settings_row.default_symbol]},
    )

    def fail_execute(*args, **kwargs):
        raise AssertionError("historical replay must not place live orders")

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    orchestrator = TradingOrchestrator(db_session)
    _patch_entry_plan_decision_flow(monkeypatch, orchestrator)
    result = orchestrator.run_decision_cycle(trigger_event="historical_replay", upto_index=140)

    assert result["decision"]["decision"] == "long"
    assert "ENTRY_TRIGGER_NOT_MET" in result["risk_result"]["blocked_reason_codes"]
    assert result["execution"] is None
    assert result["entry_plan"] is None


def test_run_decision_cycle_passes_meta_gate_into_risk_context(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    monkeypatch.setattr(
        TradingOrchestrator,
        "run_exchange_sync_cycle",
        lambda self, **kwargs: {"status": "ok", "symbols": [settings_row.default_symbol]},
    )

    captured: dict[str, object] = {}

    def fake_agent_run(*args, **kwargs):
        snapshot = args[0]
        decision = TradeDecision(
            decision="long",
            confidence=0.68,
            symbol="BTCUSDT",
            timeframe="15m",
            entry_zone_min=snapshot.latest_price - 10.0,
            entry_zone_max=snapshot.latest_price + 10.0,
            entry_mode="pullback_confirm",
            invalidation_price=snapshot.latest_price - 300.0,
            max_chase_bps=80.0,
            idea_ttl_minutes=15,
            stop_loss=snapshot.latest_price - 300.0,
            take_profit=snapshot.latest_price + 600.0,
            max_holding_minutes=120,
            risk_pct=0.01,
            leverage=2.0,
            rationale_codes=["TEST_META_GATE"],
            explanation_short="meta gate test",
            explanation_detailed="meta gate propagation test",
        )
        return decision, "deterministic-mock", {
            "source": "deterministic",
            "decision_agreement": {
                "ai_used": True,
                "comparison_source": "deterministic_baseline_vs_ai_final",
                "level": "partial_agreement",
                "direction_match": True,
                "entry_mode_match": False,
            },
        }

    def fake_evaluate_risk(session, settings_row, decision, market_snapshot, **kwargs):
        decision_context = dict(kwargs.get("decision_context") or {})
        captured["decision_context"] = decision_context
        result = RiskCheckResult(
            allowed=False,
            decision=decision.decision,
            reason_codes=["ENTRY_TRIGGER_NOT_MET"],
            blocked_reason_codes=["ENTRY_TRIGGER_NOT_MET"],
            adjustment_reason_codes=[],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            raw_projected_notional=0.0,
            approved_notional=0.0,
            approved_projected_notional=0.0,
            approved_qty=None,
            approved_quantity=None,
            operating_mode="hold",
            effective_leverage_cap=5.0,
            symbol_risk_tier="btc",
            exposure_metrics={},
            debug_payload={"meta_gate": dict(decision_context.get("meta_gate") or {})},
        )
        row = RiskCheck(
            symbol=decision.symbol,
            decision_run_id=kwargs.get("decision_run_id"),
            market_snapshot_id=kwargs.get("market_snapshot_id"),
            allowed=False,
            decision=decision.decision,
            reason_codes=["ENTRY_TRIGGER_NOT_MET"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload=result.model_dump(mode="json"),
        )
        session.add(row)
        session.flush()
        return result, row

    orchestrator = TradingOrchestrator(db_session)
    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_agent_run)
    monkeypatch.setattr("trading_mvp.services.orchestrator.evaluate_risk", fake_evaluate_risk)

    result = orchestrator.run_decision_cycle(
        trigger_event="manual",
        upto_index=140,
        selection_context={
            "universe_breadth": {"breadth_regime": "mixed", "directional_bias": "bullish"},
            "performance_summary": {
                "score": 0.54,
                "sample_size": 6,
                "expectancy": 4.0,
                "net_pnl_after_fees": 8.0,
                "avg_signed_slippage_bps": 9.0,
                "loss_streak": 0,
                "underperforming": False,
            },
            "score": {
                "total_score": 0.55,
                "lead_lag_alignment": 0.58,
                "derivatives_alignment": 0.49,
            },
        },
    )
    decision_run = db_session.get(AgentRun, result["decision_run_id"])

    assert captured["decision_context"] is not None
    assert dict(captured["decision_context"])["meta_gate"]["gate_decision"] == "soft_pass"
    assert decision_run is not None
    assert decision_run.metadata_json["meta_gate"]["gate_decision"] == "soft_pass"
    assert result["risk_result"]["debug_payload"]["meta_gate"]["gate_decision"] == "soft_pass"


def test_runtime_market_snapshot_requires_binance_data() -> None:
    try:
        build_market_snapshot(symbol="BTCUSDT", timeframe="15m", use_binance=False)
    except RuntimeError as exc:
        assert "Binance 실데이터" in str(exc)
    else:
        raise AssertionError("runtime snapshot should not fall back to synthetic data")


def test_live_sync_persists_partial_fill_and_position(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    order = Order(
        symbol="BTCUSDT",
        decision_run_id=None,
        risk_check_id=None,
        position_id=None,
        side="long",
        order_type="market",
        mode="live",
        status="pending",
        external_order_id="1001",
        client_order_id="mvp-test",
        reduce_only=False,
        close_only=False,
        parent_order_id=None,
        exchange_status="NEW",
        last_exchange_update_at=None,
        requested_quantity=0.5,
        requested_price=65000.0,
        filled_quantity=0.0,
        average_fill_price=0.0,
        reason_codes=[],
        metadata_json={},
    )
    db_session.add(order)
    db_session.flush()

    class FakeClient:
        def get_account_info(self):
            return {
                "availableBalance": "100.0",
                "totalWalletBalance": "100.0",
                "totalUnrealizedProfit": "2.5",
                "totalMarginBalance": "102.5",
            }

        def get_order(self, *, symbol, order_id=None, client_order_id=None):
            return {"orderId": "1001", "status": "PARTIALLY_FILLED", "executedQty": "0.25", "avgPrice": "65100"}

        def get_account_trades(self, *, symbol, order_id=None, limit=50):
            return [
                {
                    "id": 9001,
                    "price": "65100",
                    "qty": "0.25",
                    "commission": "1.2",
                    "commissionAsset": "USDT",
                    "realizedPnl": "0",
                }
            ]

        def get_open_orders(self, symbol=None):
            return [
                {"type": "STOP_MARKET", "stopPrice": "64000", "closePosition": True},
                {"type": "TAKE_PROFIT_MARKET", "stopPrice": "66500", "closePosition": True},
            ]

        def get_position_information(self, symbol=None):
            return [{"positionAmt": "0.25", "entryPrice": "65100", "markPrice": "65200", "leverage": "2"}]

    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings_row: FakeClient())

    result = sync_live_state(db_session, settings_row, symbol="BTCUSDT")
    db_session.commit()
    db_session.refresh(order)

    assert result["synced_orders"] == 1
    assert order.status == "partially_filled"
    assert order.filled_quantity == 0.25
    assert db_session.scalar(select(Execution).where(Execution.external_trade_id == "9001").limit(1)) is not None


def test_live_test_order_auto_adjusts_to_min_notional(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)

    class FakeClient:
        def get_symbol_filters(self, symbol):
            return {"tick_size": 0.1, "step_size": 0.001, "min_qty": 0.001, "min_notional": 100.0}

        def get_symbol_price(self, symbol):
            return 70000.0

        def normalize_order_quantity(self, symbol, quantity, *, reference_price=None, enforce_min_notional=True):
            assert symbol == "BTCUSDT"
            assert quantity == 0.001
            assert reference_price == 70000.0
            assert enforce_min_notional is True
            return 0.002

        def test_new_order(self, *, symbol, side, quantity):
            assert symbol == "BTCUSDT"
            assert side == "BUY"
            assert quantity == 0.002
            return {}

    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings_row: FakeClient())

    from trading_mvp.services.execution import run_live_test_order

    result = run_live_test_order(db_session, settings_row, symbol="BTCUSDT", side="BUY", quantity=0.001)

    assert result["requested_quantity"] == 0.001
    assert result["quantity"] == 0.002
    assert result["reference_price"] == 70000.0
    assert result["min_notional"] == 100.0


def test_execute_live_trade_uses_exchange_available_balance_for_sizing(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    captured: dict[str, float] = {}

    class FakeClient:
        def get_account_info(self):
            return {
                "availableBalance": "140.0",
                "totalWalletBalance": "140.0",
                "totalUnrealizedProfit": "0.0",
                "totalMarginBalance": "140.0",
            }

        def get_open_orders(self, symbol=None):
            return []

        def get_position_information(self, symbol=None):
            return []

        def change_initial_leverage(self, symbol, leverage):
            return {"leverage": leverage}

        def normalize_order_quantity(self, symbol, quantity, *, reference_price=None, enforce_min_notional=True):
            captured["requested_quantity"] = quantity
            return quantity

        def new_order(self, **kwargs):
            captured["submitted_quantity"] = kwargs["quantity"]
            return {
                "orderId": "3001",
                "clientOrderId": kwargs.get("client_order_id", "client"),
                "status": "FILLED",
                "executedQty": str(kwargs["quantity"]),
                "avgPrice": "70000",
            }

        def get_account_trades(self, *, symbol, order_id=None, limit=50):
            return []

    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: FakeClient())

    decision = TradeDecision(
        decision="long",
        confidence=0.8,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=70000.0,
        entry_zone_max=70000.0,
        stop_loss=None,
        take_profit=None,
        max_holding_minutes=60,
        risk_pct=0.01,
        leverage=1.0,
        rationale_codes=["TEST"],
        explanation_short="테스트 진입입니다.",
        explanation_detailed="실계좌 available balance 기준으로 sizing 되는지 확인합니다.",
    )
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=69900.0,
                high=70100.0,
                low=69800.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )
    risk_result = RiskCheckResult(
        allowed=True,
        decision="long",
        reason_codes=[],
        approved_risk_pct=0.01,
        approved_leverage=1.0,
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=1,
        decision=decision,
        market_snapshot=snapshot,
        risk_result=risk_result,
    )

    assert result["status"] == "filled"
    assert captured["requested_quantity"] == pytest.approx(0.002, rel=1e-6)
    assert captured["submitted_quantity"] == pytest.approx(0.002, rel=1e-6)


def test_execute_live_trade_rejects_insufficient_margin_without_raising(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)

    class FakeClient:
        def get_account_info(self):
            return {
                "availableBalance": "120.0",
                "totalWalletBalance": "120.0",
                "totalUnrealizedProfit": "0.0",
                "totalMarginBalance": "120.0",
            }

        def get_open_orders(self, symbol=None):
            return []

        def get_position_information(self, symbol=None):
            return []

        def change_initial_leverage(self, symbol, leverage):
            return {"leverage": leverage}

        def normalize_order_quantity(self, symbol, quantity, *, reference_price=None, enforce_min_notional=True):
            return quantity

        def new_order(self, **kwargs):
            raise BinanceAPIError(-2019, "Margin is insufficient.")

    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: FakeClient())

    decision = TradeDecision(
        decision="long",
        confidence=0.8,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=70000.0,
        entry_zone_max=70000.0,
        stop_loss=None,
        take_profit=None,
        max_holding_minutes=60,
        risk_pct=0.01,
        leverage=1.0,
        rationale_codes=["TEST"],
        explanation_short="테스트 진입입니다.",
        explanation_detailed="증거금 부족이 rejected 처리되는지 확인합니다.",
    )
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=69900.0,
                high=70100.0,
                low=69800.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )
    risk_result = RiskCheckResult(
        allowed=True,
        decision="long",
        reason_codes=[],
        approved_risk_pct=0.01,
        approved_leverage=1.0,
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=1,
        decision=decision,
        market_snapshot=snapshot,
        risk_result=risk_result,
    )

    order = db_session.scalar(select(Order).order_by(Order.id.desc()).limit(1))
    alert = db_session.scalar(select(AuditEvent).where(AuditEvent.event_type == "live_execution_rejected").limit(1))

    assert result["status"] == "rejected"
    assert "INSUFFICIENT_MARGIN" in result["reason_codes"]
    assert order is not None
    assert order.status == "rejected"
    assert alert is not None


def test_run_selected_symbols_cycle_isolates_symbol_failures(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    def fake_run_decision_cycle(self, symbol=None, **kwargs):
        if symbol == "BTCUSDT":
            raise RuntimeError("boom")
        return {"symbol": symbol, "status": "ok"}

    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)

    result = TradingOrchestrator(db_session).run_selected_symbols_cycle(trigger_event="realtime_cycle")

    assert result["failed_symbols"] == ["BTCUSDT"]
    assert result["results"][0]["status"] == "failed"
    assert result["results"][1]["status"] == "ok"


def test_run_selected_symbols_cycle_uses_candidate_selection_top_n(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    db_session.add(settings_row)
    db_session.flush()
    executed_symbols: list[str] = []
    selection_contexts: dict[str, dict[str, object]] = {}

    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "max_selected": 2,
            "breadth_regime": "mixed",
            "breadth_summary": {
                "breadth_regime": "mixed",
                "bullish_aligned_count": 2,
                "bearish_aligned_count": 1,
                "weak_volume_count": 1,
                "transition_count": 1,
            },
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "entry_score_threshold": 0.4,
            "portfolio_allocator": {
                "allocator_mode": "slot_weighted_rotation",
                "slot_mode": "conviction_slots",
                "slot_plan": {
                    "available_slots": ["slot_1", "slot_2"],
                    "high_conviction_threshold": 0.68,
                    "medium_conviction_threshold": 0.54,
                    "low_conviction_action": "exclude",
                },
                "selected_entry_symbols": ["BTCUSDT", "ETHUSDT"],
                "weights": {"BTCUSDT": 0.64, "ETHUSDT": 0.36},
                "slot_assignments": {
                    "slot_1": {
                        "label": "high_conviction",
                        "symbol": "BTCUSDT",
                        "candidate_weight": 0.64,
                        "slot_conviction_score": 0.83,
                        "meta_gate_probability": 0.74,
                    },
                    "slot_2": {
                        "label": "medium_conviction",
                        "symbol": "ETHUSDT",
                        "candidate_weight": 0.36,
                        "slot_conviction_score": 0.63,
                        "meta_gate_probability": 0.58,
                    },
                },
            },
            "selected_symbols": ["BTCUSDT", "ETHUSDT"],
            "skipped_symbols": ["BNBUSDT"],
            "rankings": [
                {
                    "symbol": "BTCUSDT",
                    "selected": True,
                    "selected_reason": "ranked_portfolio_focus",
                    "selection_reason": "ranked_portfolio_focus",
                    "holding_profile": "scalp",
                    "holding_profile_reason": "scalp_default_intraday_bias",
                    "holding_profile_context": {
                        "holding_profile": "scalp",
                        "holding_profile_reason": "scalp_default_intraday_bias",
                        "cadence_hint": {"decision_interval_minutes": 15},
                    },
                    "breadth_score_multiplier": 1.04,
                    "breadth_score_adjustment": 0.04,
                    "breadth_hold_bias": 0.95,
                    "breadth_adjustment_reasons": ["breadth_trend_alignment_boost"],
                    "portfolio_weight": 0.64,
                    "candidate_weight": 0.64,
                    "assigned_slot": "slot_1",
                    "slot_label": "high_conviction",
                    "slot_reason": "high_conviction_slot",
                    "slot_conviction_score": 0.83,
                    "meta_gate_probability": 0.74,
                    "agreement_alignment_score": 0.79,
                    "agreement_level_hint": "full_agreement_likely",
                    "execution_quality_score": 0.77,
                    "slot_risk_pct_multiplier": 1.0,
                    "slot_leverage_multiplier": 1.0,
                    "slot_notional_multiplier": 1.0,
                    "score": {"total_score": 0.71, "correlation_penalty": 0.0},
                },
                {
                    "symbol": "ETHUSDT",
                    "selected": True,
                    "selected_reason": "ranked_portfolio_focus",
                    "selection_reason": "ranked_portfolio_focus",
                    "holding_profile": "swing",
                    "holding_profile_reason": "swing_intraday_trend_extension_allowed",
                    "holding_profile_context": {
                        "holding_profile": "swing",
                        "holding_profile_reason": "swing_intraday_trend_extension_allowed",
                        "cadence_hint": {"decision_interval_minutes": 20},
                    },
                    "breadth_score_multiplier": 1.04,
                    "breadth_score_adjustment": 0.0,
                    "breadth_hold_bias": 0.95,
                    "breadth_adjustment_reasons": [],
                    "portfolio_weight": 0.36,
                    "candidate_weight": 0.36,
                    "assigned_slot": "slot_2",
                    "slot_label": "medium_conviction",
                    "slot_reason": "medium_conviction_slot",
                    "slot_conviction_score": 0.63,
                    "meta_gate_probability": 0.58,
                    "agreement_alignment_score": 0.61,
                    "agreement_level_hint": "partial_agreement_likely",
                    "execution_quality_score": 0.59,
                    "slot_risk_pct_multiplier": 0.82,
                    "slot_leverage_multiplier": 0.9,
                    "slot_notional_multiplier": 0.78,
                    "score": {"total_score": 0.58, "correlation_penalty": 0.14},
                },
                {
                    "symbol": "BNBUSDT",
                    "selected": False,
                    "selection_reason": "low_conviction_slot_excluded",
                    "rejected_reason": "low_conviction_slot_excluded",
                    "breadth_score_multiplier": 0.98,
                    "breadth_score_adjustment": 0.0,
                    "breadth_hold_bias": 1.0,
                    "breadth_adjustment_reasons": [],
                    "portfolio_weight": 0.0,
                    "score": {"total_score": 0.22, "correlation_penalty": 0.36},
                },
            ],
        },
    )

    def fake_run_decision_cycle(self, symbol=None, **kwargs):
        executed_symbols.append(str(symbol))
        selection_contexts[str(symbol)] = dict(kwargs.get("selection_context") or {})
        return {"symbol": symbol, "status": "ok"}

    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)

    result = TradingOrchestrator(db_session).run_selected_symbols_cycle(trigger_event="realtime_cycle")

    assert executed_symbols == ["BTCUSDT", "ETHUSDT"]
    assert result["tracked_symbols"] == ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    assert result["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert result["candidate_selection"]["mode"] == "portfolio_rotation_top_n"
    assert result["candidate_selection"]["breadth_regime"] == "mixed"
    assert result["candidate_selection"]["capacity_reason"] == "mixed_breadth_moderate_capacity"
    assert result["candidate_selection"]["selected_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert result["candidate_selection"]["skipped_symbols"] == ["BNBUSDT"]
    assert result["candidate_selection"]["portfolio_allocator"]["allocator_mode"] == "slot_weighted_rotation"
    assert result["candidate_selection"]["portfolio_allocator"]["weights"]["BTCUSDT"] == 0.64
    assert result["candidate_selection"]["portfolio_allocator"]["slot_assignments"]["slot_1"]["symbol"] == "BTCUSDT"
    assert result["candidate_selection"]["rankings"][0]["selected_reason"] == "ranked_portfolio_focus"
    assert result["candidate_selection"]["rankings"][2]["score"]["correlation_penalty"] == 0.36
    assert selection_contexts["BTCUSDT"]["universe_breadth"]["breadth_regime"] == "mixed"
    assert selection_contexts["BTCUSDT"]["portfolio_weight"] == 0.64
    assert selection_contexts["BTCUSDT"]["candidate_weight"] == 0.64
    assert selection_contexts["BTCUSDT"]["assigned_slot"] == "slot_1"
    assert selection_contexts["BTCUSDT"]["holding_profile"] == "scalp"
    assert selection_contexts["BTCUSDT"]["holding_profile_reason"] == "scalp_default_intraday_bias"
    assert selection_contexts["BTCUSDT"]["holding_profile_context"]["holding_profile"] == "scalp"
    assert selection_contexts["BTCUSDT"]["slot_allocation"]["assigned_slot"] == "slot_1"
    assert selection_contexts["ETHUSDT"]["slot_notional_multiplier"] == 0.78
    assert selection_contexts["ETHUSDT"]["holding_profile"] == "swing"
    assert selection_contexts["BTCUSDT"]["breadth_score_multiplier"] == 1.04
    assert selection_contexts["BTCUSDT"]["breadth_adjustment_reasons"] == ["breadth_trend_alignment_boost"]


def test_rank_candidate_symbols_reduces_capacity_on_weak_breadth(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    candidates = {
        "BTCUSDT": _selection_candidate_row(
            symbol="BTCUSDT",
            decision="long",
            scenario="trend_follow",
            total_score=0.82,
            recent_signal_performance=0.84,
            derivatives_alignment=0.76,
            lead_lag_alignment=0.78,
            slippage_sensitivity=0.74,
            confidence_consistency=0.78,
            weak_volume=True,
            primary_regime="range",
            momentum_weakening=True,
            returns=[0.02, 0.015, 0.01, 0.005],
        ),
        "ETHUSDT": _selection_candidate_row(
            symbol="ETHUSDT",
            decision="long",
            scenario="trend_follow",
            total_score=0.66,
            recent_signal_performance=0.68,
            derivatives_alignment=0.62,
            lead_lag_alignment=0.64,
            slippage_sensitivity=0.6,
            confidence_consistency=0.62,
            weak_volume=True,
            primary_regime="range",
            momentum_weakening=True,
            returns=[0.02, 0.015, 0.01, 0.005],
        ),
        "BNBUSDT": _selection_candidate_row(
            symbol="BNBUSDT",
            decision="long",
            scenario="pullback_entry",
            total_score=0.57,
            weak_volume=False,
            primary_regime="transition",
            momentum_weakening=True,
            returns=[0.012, -0.008, 0.009, -0.005, 0.007, -0.003],
        ),
    }

    monkeypatch.setattr(
        TradingOrchestrator,
        "_build_selection_candidate",
        lambda self, *, symbol, **kwargs: candidates[str(symbol).upper()],
    )

    result = TradingOrchestrator(db_session)._rank_candidate_symbols(
        decision_symbols=["BTCUSDT", "ETHUSDT", "BNBUSDT"],
        timeframe="15m",
        upto_index=None,
        force_stale=False,
    )

    assert result["mode"] == "portfolio_rotation_top_n"
    assert result["breadth_regime"] == "weak_breadth"
    assert result["max_selected"] == 1
    assert result["capacity_reason"] == "breadth_weak_reduce_capacity"
    assert result["selected_symbols"] == ["BTCUSDT"]
    eth_ranking = next(item for item in result["rankings"] if item["symbol"] == "ETHUSDT")
    bnb_ranking = next(item for item in result["rankings"] if item["symbol"] == "BNBUSDT")
    assert eth_ranking["rejected_reason"] in {"correlation_limit", "duplicate_scenario_exposure"}
    assert bnb_ranking["rejected_reason"] == "capacity_reached"
    assert eth_ranking["breadth_score_multiplier"] < 1.0
    assert "weak_breadth_structure_penalty" in eth_ranking["breadth_adjustment_reasons"]
    assert result["portfolio_allocator"]["weights"] == {"BTCUSDT": 1.0}


def test_selection_capacity_plan_applies_drawdown_overlay_without_touching_priority_slots(db_session) -> None:
    breadth_summary = {"breadth_regime": "trend_expansion"}
    drawdown_state = {
        "current_drawdown_state": "drawdown_containment",
        "policy_adjustments": {
            "max_non_priority_selected": 1,
            "entry_score_threshold_uplift": 0.08,
        },
    }

    max_selected, entry_score_threshold, capacity_reason, drawdown_capacity_reason = TradingOrchestrator._selection_capacity_plan(
        breadth_summary=breadth_summary,
        priority_count=2,
        candidate_count=5,
        drawdown_state=drawdown_state,
    )

    assert max_selected == 3
    assert entry_score_threshold == 0.44
    assert capacity_reason == "trend_expansion_allow_rotation"
    assert drawdown_capacity_reason == "drawdown_containment_reduce_capacity"


def test_rank_candidate_symbols_weights_higher_scores_more_aggressively(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    candidates = {
        "BTCUSDT": _selection_candidate_row(
            symbol="BTCUSDT",
            decision="long",
            scenario="trend_follow",
            total_score=0.92,
            returns=[0.02, 0.0, 0.01, 0.03],
        ),
        "ETHUSDT": _selection_candidate_row(
            symbol="ETHUSDT",
            decision="long",
            scenario="pullback_entry",
            total_score=0.61,
            returns=[-0.012, 0.018, -0.009, 0.015, -0.006, 0.012],
        ),
    }

    monkeypatch.setattr(
        TradingOrchestrator,
        "_build_selection_candidate",
        lambda self, *, symbol, **kwargs: candidates[str(symbol).upper()],
    )

    result = TradingOrchestrator(db_session)._rank_candidate_symbols(
        decision_symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="15m",
        upto_index=None,
        force_stale=False,
    )

    assert result["breadth_regime"] == "trend_expansion"
    assert result["capacity_reason"] == "trend_expansion_allow_rotation"
    weights = result["portfolio_allocator"]["weights"]
    assert weights["BTCUSDT"] > weights["ETHUSDT"]
    btc_ranking = next(item for item in result["rankings"] if item["symbol"] == "BTCUSDT")
    eth_ranking = next(item for item in result["rankings"] if item["symbol"] == "ETHUSDT")
    assert btc_ranking["assigned_slot"] == "slot_1"
    assert eth_ranking["assigned_slot"] == "slot_2"
    assert btc_ranking["candidate_weight"] > eth_ranking["candidate_weight"]
    assert btc_ranking["portfolio_weight"] > eth_ranking["portfolio_weight"]
    assert btc_ranking["breadth_score_adjustment"] >= 0.0
    assert result["portfolio_allocator"]["slot_assignments"]["slot_1"]["symbol"] == "BTCUSDT"
    assert all(item["selected"] for item in result["rankings"])


def test_rank_candidate_symbols_prefers_high_expectancy_candidates(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    now = utcnow_naive()

    def make_snapshot(symbol: str) -> MarketSnapshotPayload:
        candles = [
            MarketCandle(
                timestamp=now - timedelta(minutes=15 * (6 - index)),
                open=100.0 + (index * 0.5),
                high=100.5 + (index * 0.5),
                low=99.8 + (index * 0.5),
                close=100.2 + (index * 0.5),
                volume=1000.0 + (index * 50.0),
            )
            for index in range(6)
        ]
        return MarketSnapshotPayload(
            symbol=symbol,
            timeframe="15m",
            snapshot_time=now,
            latest_price=candles[-1].close,
            latest_volume=candles[-1].volume,
            candle_count=len(candles),
            is_stale=False,
            is_complete=True,
            candles=candles,
        )

    contexts = {
        "BTCUSDT": {
            "15m": make_snapshot("BTCUSDT"),
            "1h": make_snapshot("BTCUSDT").model_copy(update={"timeframe": "1h"}),
            "4h": make_snapshot("BTCUSDT").model_copy(update={"timeframe": "4h"}),
        },
        "ETHUSDT": {
            "15m": make_snapshot("ETHUSDT"),
            "1h": make_snapshot("ETHUSDT").model_copy(update={"timeframe": "1h"}),
            "4h": make_snapshot("ETHUSDT").model_copy(update={"timeframe": "4h"}),
        },
    }

    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda symbol, **kwargs: contexts[str(symbol).upper()],
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_recent_signal_performance_summary",
        lambda self, *, symbol, **kwargs: {
            "score": 0.88 if str(symbol).upper() == "BTCUSDT" else 0.41,
            "sample_size": 6,
            "hit_rate": 0.66 if str(symbol).upper() == "BTCUSDT" else 0.5,
            "expectancy": 18.4 if str(symbol).upper() == "BTCUSDT" else -2.5,
            "net_pnl_after_fees": 74.2 if str(symbol).upper() == "BTCUSDT" else -10.0,
            "avg_signed_slippage_bps": 3.2 if str(symbol).upper() == "BTCUSDT" else 11.8,
            "loss_streak": 0 if str(symbol).upper() == "BTCUSDT" else 2,
            "underperforming": False,
            "components": {},
        },
    )
    monkeypatch.setattr(TradingOrchestrator, "_slippage_sensitivity_score", lambda self, symbol: 0.5)
    monkeypatch.setattr(TradingOrchestrator, "_confidence_consistency_score", lambda self, symbol, decision: 0.5)
    monkeypatch.setattr(TradingOrchestrator, "_candidate_exposure_impact_score", lambda self, **kwargs: 0.5)

    orchestrator = TradingOrchestrator(db_session)
    btc_candidate = orchestrator._build_selection_candidate(
        symbol="BTCUSDT",
        timeframe="15m",
        upto_index=None,
        force_stale=False,
        missing_protection_symbols=set(),
        total_open_positions=0,
    )
    eth_candidate = orchestrator._build_selection_candidate(
        symbol="ETHUSDT",
        timeframe="15m",
        upto_index=None,
        force_stale=False,
        missing_protection_symbols=set(),
        total_open_positions=0,
    )

    assert btc_candidate["performance_summary"]["expectancy"] == 18.4
    assert eth_candidate["performance_summary"]["expectancy"] == -2.5
    assert btc_candidate["score"].recent_signal_performance > eth_candidate["score"].recent_signal_performance
    assert btc_candidate["score"].total_score > eth_candidate["score"].total_score


def test_build_selection_candidate_attaches_strategy_engine_and_engine_bucket(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    captured: dict[str, object] = {}
    now = utcnow_naive()

    def _snapshot(symbol: str, timeframe: str, closes: list[float]) -> MarketSnapshotPayload:
        candles = [
            MarketCandle(
                timestamp=now - timedelta(minutes=15 * (len(closes) - index)),
                open=closes[index - 1] if index > 0 else closes[index],
                high=max(closes[index - 1] if index > 0 else closes[index], close) * 1.002,
                low=min(closes[index - 1] if index > 0 else closes[index], close) * 0.998,
                close=close,
                volume=900.0 + (index * 20.0),
            )
            for index, close in enumerate(closes)
        ]
        return MarketSnapshotPayload(
            symbol=symbol,
            timeframe=timeframe,
            snapshot_time=now,
            latest_price=closes[-1],
            latest_volume=candles[-1].volume,
            candle_count=len(candles),
            is_stale=False,
            is_complete=True,
            candles=candles,
            derivatives_context=DerivativesContextPayload(),
        )

    def fake_build_market_context(*, symbol: str, base_timeframe: str, **kwargs):
        return {
            base_timeframe: _snapshot(symbol, base_timeframe, [100.0, 100.4, 100.7, 101.1, 101.4, 101.9, 102.3, 102.8, 103.2, 103.6, 104.1, 104.5, 104.9, 105.3, 105.8, 106.2]),
            "1h": _snapshot(symbol, "1h", [98.0, 98.7, 99.4, 100.1, 100.9, 101.8, 102.7, 103.6, 104.4, 105.2, 106.1, 107.0, 108.0, 109.1, 110.2, 111.4]),
            "4h": _snapshot(symbol, "4h", [92.0, 93.4, 94.9, 96.6, 98.4, 100.1, 102.0, 104.1, 106.4, 108.8, 111.3, 114.0, 116.8, 119.7, 122.7, 125.8]),
        }

    def fake_recent_signal_performance_summary(self, **kwargs):
        captured.update(kwargs)
        return {
            "score": 0.74,
            "sample_size": 6,
            "hit_rate": 0.66,
            "expectancy": 11.0,
            "net_pnl_after_fees": 42.0,
            "avg_signed_slippage_bps": 4.0,
            "avg_time_to_profit_minutes": 22.0,
            "avg_drawdown_impact": 0.28,
            "loss_streak": 0,
            "underperforming": False,
            "components": {
                "symbol": {"label": "symbol", "sample_size": 6, "score": 0.71},
                "engine": {"label": "engine", "sample_size": 4, "score": 0.76},
                "scenario": {"label": "scenario", "sample_size": 6, "score": 0.73},
                "regime": {"label": "regime", "sample_size": 6, "score": 0.72},
                "bucket": {"label": "bucket", "sample_size": 4, "score": 0.75},
            },
        }

    monkeypatch.setattr(TradingOrchestrator, "_recent_signal_performance_summary", fake_recent_signal_performance_summary)
    monkeypatch.setattr(TradingOrchestrator, "_slippage_sensitivity_score", lambda self, symbol: 0.5)
    monkeypatch.setattr(TradingOrchestrator, "_confidence_consistency_score", lambda self, symbol, decision: 0.5)
    monkeypatch.setattr(TradingOrchestrator, "_candidate_exposure_impact_score", lambda self, **kwargs: 0.5)
    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_context", fake_build_market_context)

    candidate_row = TradingOrchestrator(db_session)._build_selection_candidate(
        symbol="BTCUSDT",
        timeframe="15m",
        upto_index=None,
        force_stale=False,
        missing_protection_symbols=set(),
        total_open_positions=0,
    )

    assert candidate_row["candidate"].strategy_engine == "trend_continuation_engine"
    assert candidate_row["strategy_engine"] == "trend_continuation_engine"
    assert candidate_row["candidate"].strategy_engine_context["selected_engine"]["engine_name"] == "trend_continuation_engine"
    assert captured["strategy_engine"] == "trend_continuation_engine"
    assert candidate_row["performance_summary"]["components"]["engine"]["label"] == "engine"



def test_rank_candidate_symbols_rejects_low_expectancy_before_correlation(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    shared_returns = [0.02, 0.018, 0.015, 0.012, 0.01, 0.008]
    candidates = {
        "BTCUSDT": _selection_candidate_row(
            symbol="BTCUSDT",
            decision="long",
            scenario="trend_follow",
            total_score=0.76,
            returns=shared_returns,
            performance_summary={
                "score": 0.82,
                "sample_size": 6,
                "hit_rate": 0.66,
                "expectancy": 12.0,
                "net_pnl_after_fees": 48.0,
                "avg_signed_slippage_bps": 4.0,
                "loss_streak": 0,
                "underperforming": False,
                "components": {},
            },
        ),
        "ETHUSDT": _selection_candidate_row(
            symbol="ETHUSDT",
            decision="long",
            scenario="trend_follow",
            total_score=0.79,
            returns=shared_returns,
            performance_summary={
                "score": 0.31,
                "sample_size": 6,
                "hit_rate": 0.16,
                "expectancy": -14.0,
                "net_pnl_after_fees": -56.0,
                "avg_signed_slippage_bps": 14.5,
                "loss_streak": 4,
                "underperforming": True,
                "components": {},
            },
        ),
    }

    monkeypatch.setattr(
        TradingOrchestrator,
        "_build_selection_candidate",
        lambda self, *, symbol, **kwargs: candidates[str(symbol).upper()],
    )

    result = TradingOrchestrator(db_session)._rank_candidate_symbols(
        decision_symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="15m",
        upto_index=None,
        force_stale=False,
    )

    eth_ranking = next(item for item in result["rankings"] if item["symbol"] == "ETHUSDT")
    assert eth_ranking["rejected_reason"] == "underperforming_expectancy_bucket"
    assert eth_ranking["rejected_reason"] != "correlation_limit"


def test_rank_candidate_symbols_excludes_low_conviction_candidates_from_slots(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    candidates = {
        "BTCUSDT": _selection_candidate_row(
            symbol="BTCUSDT",
            decision="long",
            scenario="trend_follow",
            total_score=0.88,
            returns=[0.03, 0.026, 0.022, 0.018],
            performance_summary={
                "score": 0.84,
                "sample_size": 6,
                "hit_rate": 0.68,
                "expectancy": 16.0,
                "net_pnl_after_fees": 54.0,
                "avg_signed_slippage_bps": 3.5,
                "loss_streak": 0,
                "underperforming": False,
                "components": {},
            },
            recent_signal_performance=0.84,
            derivatives_alignment=0.74,
            lead_lag_alignment=0.76,
            slippage_sensitivity=0.72,
            confidence_consistency=0.74,
        ),
        "ETHUSDT": _selection_candidate_row(
            symbol="ETHUSDT",
            decision="long",
            scenario="trend_follow",
            total_score=0.67,
            returns=[0.018, -0.006, 0.012, -0.003],
            performance_summary={
                "score": 0.42,
                "sample_size": 4,
                "hit_rate": 0.25,
                "expectancy": 1.0,
                "net_pnl_after_fees": 2.0,
                "avg_signed_slippage_bps": 8.0,
                "loss_streak": 1,
                "underperforming": False,
                "components": {},
            },
            recent_signal_performance=0.48,
            derivatives_alignment=0.22,
            lead_lag_alignment=0.25,
            slippage_sensitivity=0.3,
            confidence_consistency=0.32,
        ),
    }

    monkeypatch.setattr(
        TradingOrchestrator,
        "_build_selection_candidate",
        lambda self, *, symbol, **kwargs: candidates[str(symbol).upper()],
    )

    result = TradingOrchestrator(db_session)._rank_candidate_symbols(
        decision_symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="15m",
        upto_index=None,
        force_stale=False,
    )

    btc_ranking = next(item for item in result["rankings"] if item["symbol"] == "BTCUSDT")
    eth_ranking = next(item for item in result["rankings"] if item["symbol"] == "ETHUSDT")
    assert btc_ranking["assigned_slot"] == "slot_1"
    assert eth_ranking["selected"] is False
    assert eth_ranking["rejected_reason"] == "low_conviction_slot_excluded"
    assert eth_ranking["assigned_slot"] is None
    assert eth_ranking["slot_conviction_score"] < 0.54
    assert result["selected_symbols"] == ["BTCUSDT"]


def test_rank_candidate_symbols_preserves_priority_symbols_during_rotation(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    candidates = {
        "BTCUSDT": _selection_candidate_row(
            symbol="BTCUSDT",
            decision="reduce",
            scenario="reduce",
            total_score=0.12,
            priority=True,
            trend_alignment="bullish_aligned",
            primary_regime="range",
            weak_volume=True,
            momentum_weakening=True,
            returns=[0.0, 0.0, 0.0],
        ),
        "ETHUSDT": _selection_candidate_row(
            symbol="ETHUSDT",
            decision="long",
            scenario="trend_follow",
            total_score=0.86,
            recent_signal_performance=0.84,
            derivatives_alignment=0.78,
            lead_lag_alignment=0.8,
            slippage_sensitivity=0.76,
            confidence_consistency=0.8,
            weak_volume=True,
            primary_regime="range",
            momentum_weakening=True,
            returns=[0.03, 0.025, 0.02, 0.015],
        ),
        "SOLUSDT": _selection_candidate_row(
            symbol="SOLUSDT",
            decision="long",
            scenario="pullback_entry",
            total_score=0.69,
            weak_volume=True,
            primary_regime="range",
            momentum_weakening=True,
            returns=[0.031, 0.026, 0.021, 0.016],
        ),
    }

    monkeypatch.setattr(
        TradingOrchestrator,
        "_build_selection_candidate",
        lambda self, *, symbol, **kwargs: candidates[str(symbol).upper()],
    )

    result = TradingOrchestrator(db_session)._rank_candidate_symbols(
        decision_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        timeframe="15m",
        upto_index=None,
        force_stale=False,
    )

    assert result["breadth_regime"] == "weak_breadth"
    assert result["selected_symbols"][0] == "BTCUSDT"
    assert "ETHUSDT" in result["selected_symbols"]
    priority_ranking = next(item for item in result["rankings"] if item["symbol"] == "BTCUSDT")
    skipped_ranking = next(item for item in result["rankings"] if item["symbol"] == "SOLUSDT")
    assert priority_ranking["selected_reason"] == "priority_position_or_protection"
    assert priority_ranking["portfolio_weight"] == 0.0
    assert priority_ranking["assigned_slot"] == "priority_management"
    assert skipped_ranking["rejected_reason"] in {
        "capacity_reached",
        "correlation_limit",
        "duplicate_scenario_exposure",
        "duplicate_directional_exposure",
    }


def test_orchestrator_builds_active_setup_cluster_context_for_repeated_losses(db_session) -> None:
    _seed_setup_cluster_history(
        db_session,
        net_pnls=[-28.0, -22.0, -18.0, -14.0],
        signed_slippage_bps=[16.0, 15.0, 14.0, 13.0],
        entry_mode="pullback_confirm",
        primary_regime="bullish",
        trend_alignment="bullish_aligned",
    )
    _seed_setup_cluster_history(
        db_session,
        net_pnls=[18.0, 14.0, 12.0, 10.0],
        signed_slippage_bps=[4.0, 4.5, 5.0, 4.0],
        entry_mode="pullback_confirm",
        primary_regime="bullish",
        trend_alignment="bearish_aligned",
        start_offset_minutes=60,
    )

    context = TradingOrchestrator(db_session)._build_setup_cluster_context(
        symbol="BTCUSDT",
        timeframe="15m",
        regime="bullish",
        trend_alignment="bullish_aligned",
    )
    cluster_key = "BTCUSDT|15m|pullback_entry|pullback_confirm|bullish|bullish_aligned"
    other_cluster_key = "BTCUSDT|15m|pullback_entry|pullback_confirm|bullish|bearish_aligned"

    assert cluster_key in context["cluster_lookup"]
    assert context["cluster_lookup"][cluster_key]["active"] is True
    assert context["cluster_lookup"][cluster_key]["status"] == "active_disabled"
    assert context["cluster_lookup"][cluster_key]["cooldown_active"] is True
    assert context["cluster_lookup"][cluster_key]["metrics"]["loss_streak"] == 4
    assert context["cluster_lookup"][cluster_key]["thresholds"]["loss_streak"] == 3
    assert other_cluster_key in context["cluster_lookup"]
    assert context["cluster_lookup"][other_cluster_key]["active"] is False


def test_orchestrator_setup_cluster_context_recovers_after_cooldown(db_session) -> None:
    _seed_setup_cluster_history(
        db_session,
        net_pnls=[-28.0, -22.0, -18.0, -14.0],
        signed_slippage_bps=[16.0, 15.0, 14.0, 13.0],
        entry_mode="pullback_confirm",
        primary_regime="bullish",
        trend_alignment="bullish_aligned",
        start_offset_minutes=240,
    )

    context = TradingOrchestrator(db_session)._build_setup_cluster_context(
        symbol="BTCUSDT",
        timeframe="15m",
        regime="bullish",
        trend_alignment="bullish_aligned",
    )
    cluster_key = "BTCUSDT|15m|pullback_entry|pullback_confirm|bullish|bullish_aligned"

    assert cluster_key in context["cluster_lookup"]
    assert context["cluster_lookup"][cluster_key]["active"] is False
    assert context["cluster_lookup"][cluster_key]["underperforming"] is True
    assert context["cluster_lookup"][cluster_key]["status"] == "cooldown_elapsed"
    assert context["cluster_lookup"][cluster_key]["recovery_trigger"] == "cooldown_elapsed"
    assert context["cluster_lookup"][cluster_key]["cooldown_expires_at"] is not None


def test_orchestrator_setup_cluster_context_recovers_on_positive_recent_metrics(db_session) -> None:
    _seed_setup_cluster_history(
        db_session,
        net_pnls=[18.0, 14.0, 12.0, -4.0],
        signed_slippage_bps=[4.0, 4.5, 4.0, 5.0],
        entry_mode="pullback_confirm",
        primary_regime="bullish",
        trend_alignment="bullish_aligned",
    )

    context = TradingOrchestrator(db_session)._build_setup_cluster_context(
        symbol="BTCUSDT",
        timeframe="15m",
        regime="bullish",
        trend_alignment="bullish_aligned",
    )
    cluster_key = "BTCUSDT|15m|pullback_entry|pullback_confirm|bullish|bullish_aligned"

    assert cluster_key in context["cluster_lookup"]
    assert context["cluster_lookup"][cluster_key]["active"] is False
    assert context["cluster_lookup"][cluster_key]["underperforming"] is False
    assert context["cluster_lookup"][cluster_key]["status"] == "metrics_recovered"
    assert context["cluster_lookup"][cluster_key]["recovery_trigger"] == "positive_recent_metrics"


def test_build_selection_candidate_rewards_derivatives_alignment(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    now = utcnow_naive()

    def make_snapshot(
        symbol: str,
        *,
        taker_imbalance: float,
        funding_rate: float,
        crowding_bias: float,
        oi_change: float,
        spread_bps: float = 2.0,
        top_trader_ratio: float = 1.0,
        spread_stress_score: float | None = None,
    ) -> MarketSnapshotPayload:
        closes = [100.0, 100.4, 100.8, 101.2, 101.7, 102.2, 102.8, 103.5, 104.3, 105.2, 106.2, 107.3, 108.5, 109.8, 111.2, 112.7]
        spread_abs = closes[-1] * (spread_bps / 10000.0)
        candles = [
                MarketCandle(
                    timestamp=now - timedelta(minutes=15 * (len(closes) - index)),
                    open=closes[index - 1] if index > 0 else closes[index],
                    high=max(closes[index - 1] if index > 0 else closes[index], close) * 1.003,
                    low=min(closes[index - 1] if index > 0 else closes[index], close) * 0.997,
                    close=close,
                    volume=1200.0 + (index * 25.0),
                )
            for index, close in enumerate(closes)
        ]
        return MarketSnapshotPayload(
            symbol=symbol,
            timeframe="15m",
            snapshot_time=now,
            latest_price=closes[-1],
            latest_volume=candles[-1].volume,
            candle_count=len(candles),
            is_stale=False,
            is_complete=True,
            candles=candles,
            derivatives_context=DerivativesContextPayload(
                source="binance_public",
                open_interest=150000.0,
                open_interest_change_pct=oi_change,
                funding_rate=funding_rate,
                taker_buy_sell_imbalance=taker_imbalance,
                perp_basis_bps=6.0 if taker_imbalance > 0 else -2.0,
                crowding_bias=crowding_bias,
                top_trader_long_short_ratio=top_trader_ratio,
                best_bid=closes[-1] - (spread_abs / 2.0),
                best_ask=closes[-1] + (spread_abs / 2.0),
                spread_bps=spread_bps,
                spread_stress_score=spread_stress_score,
            ),
        )

    bullish_higher = make_snapshot(
        "BTCUSDT",
        taker_imbalance=0.2,
        funding_rate=0.0001,
        crowding_bias=0.05,
        oi_change=2.0,
        spread_bps=1.8,
        top_trader_ratio=1.04,
        spread_stress_score=0.74,
    )
    contexts = {
        "BTCUSDT": {
            "15m": bullish_higher,
            "1h": bullish_higher.model_copy(update={"timeframe": "1h"}),
            "4h": bullish_higher.model_copy(update={"timeframe": "4h"}),
        },
        "ETHUSDT": {
            "15m": make_snapshot(
                "ETHUSDT",
                taker_imbalance=-0.2,
                funding_rate=0.001,
                crowding_bias=0.32,
                oi_change=-0.8,
                spread_bps=7.8,
                top_trader_ratio=1.5,
                spread_stress_score=1.57,
            ),
            "1h": bullish_higher.model_copy(update={"symbol": "ETHUSDT", "timeframe": "1h"}),
            "4h": bullish_higher.model_copy(update={"symbol": "ETHUSDT", "timeframe": "4h"}),
        },
    }

    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda symbol, **kwargs: contexts[str(symbol).upper()],
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_recent_signal_performance_summary",
        lambda self, **kwargs: {
            "score": 0.5,
            "sample_size": 0,
            "hit_rate": 0.0,
            "expectancy": 0.0,
            "net_pnl_after_fees": 0.0,
            "avg_signed_slippage_bps": 0.0,
            "loss_streak": 0,
            "underperforming": False,
            "components": {},
        },
    )
    monkeypatch.setattr(TradingOrchestrator, "_slippage_sensitivity_score", lambda self, symbol: 0.5)
    monkeypatch.setattr(TradingOrchestrator, "_confidence_consistency_score", lambda self, symbol, decision: 0.5)
    monkeypatch.setattr(TradingOrchestrator, "_candidate_exposure_impact_score", lambda self, **kwargs: 0.5)

    orchestrator = TradingOrchestrator(db_session)
    btc_candidate = orchestrator._build_selection_candidate(
        symbol="BTCUSDT",
        timeframe="15m",
        upto_index=None,
        force_stale=False,
        missing_protection_symbols=set(),
        total_open_positions=0,
    )
    eth_candidate = orchestrator._build_selection_candidate(
        symbol="ETHUSDT",
        timeframe="15m",
        upto_index=None,
        force_stale=False,
        missing_protection_symbols=set(),
        total_open_positions=0,
    )

    assert btc_candidate["score"].derivatives_alignment > eth_candidate["score"].derivatives_alignment
    assert btc_candidate["score"].total_score > eth_candidate["score"].total_score
    assert btc_candidate["candidate"].derivatives_summary["taker_flow_alignment"] == "bullish"
    assert eth_candidate["candidate"].derivatives_summary["spread_headwind"] is True
    assert eth_candidate["candidate"].derivatives_summary["spread_stress"] is True
    assert eth_candidate["candidate"].derivatives_summary["discount_magnitude"] > 0.0
    assert "SPREAD_HEADWIND" in eth_candidate["candidate"].rationale_codes
    assert "TOP_TRADER_LONG_CROWDED" in eth_candidate["candidate"].rationale_codes


def test_build_selection_candidate_discounts_funding_and_spread_headwind(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    now = utcnow_naive()

    def make_snapshot(
        symbol: str,
        *,
        taker_imbalance: float,
        funding_rate: float,
        crowding_bias: float,
        oi_change: float,
        spread_bps: float,
        top_trader_ratio: float = 1.0,
        spread_stress_score: float | None = None,
    ) -> MarketSnapshotPayload:
        closes = [100.0, 100.4, 100.8, 101.2, 101.7, 102.2, 102.8, 103.5, 104.3, 105.2, 106.2, 107.3, 108.5, 109.8, 111.2, 112.7]
        spread_abs = closes[-1] * (spread_bps / 10000.0)
        candles = [
            MarketCandle(
                timestamp=now - timedelta(minutes=15 * (len(closes) - index)),
                open=closes[index - 1] if index > 0 else closes[index],
                high=max(closes[index - 1] if index > 0 else closes[index], close) * 1.003,
                low=min(closes[index - 1] if index > 0 else closes[index], close) * 0.997,
                close=close,
                volume=1200.0 + (index * 25.0),
            )
            for index, close in enumerate(closes)
        ]
        return MarketSnapshotPayload(
            symbol=symbol,
            timeframe="15m",
            snapshot_time=now,
            latest_price=closes[-1],
            latest_volume=candles[-1].volume,
            candle_count=len(candles),
            is_stale=False,
            is_complete=True,
            candles=candles,
            derivatives_context=DerivativesContextPayload(
                source="binance_public",
                open_interest=150000.0,
                open_interest_change_pct=oi_change,
                funding_rate=funding_rate,
                taker_buy_sell_imbalance=taker_imbalance,
                perp_basis_bps=6.0 if taker_imbalance > 0 else -2.0,
                crowding_bias=crowding_bias,
                top_trader_long_short_ratio=top_trader_ratio,
                best_bid=closes[-1] - (spread_abs / 2.0),
                best_ask=closes[-1] + (spread_abs / 2.0),
                spread_bps=spread_bps,
                spread_stress_score=spread_stress_score,
            ),
        )

    contexts = {
        "BTCUSDT": {
            "15m": make_snapshot("BTCUSDT", taker_imbalance=0.22, funding_rate=0.0001, crowding_bias=0.05, oi_change=2.1, spread_bps=1.7, top_trader_ratio=1.03, spread_stress_score=0.7),
            "1h": make_snapshot("BTCUSDT", taker_imbalance=0.22, funding_rate=0.0001, crowding_bias=0.05, oi_change=2.1, spread_bps=1.7, top_trader_ratio=1.03, spread_stress_score=0.7).model_copy(update={"timeframe": "1h"}),
            "4h": make_snapshot("BTCUSDT", taker_imbalance=0.22, funding_rate=0.0001, crowding_bias=0.05, oi_change=2.1, spread_bps=1.7, top_trader_ratio=1.03, spread_stress_score=0.7).model_copy(update={"timeframe": "4h"}),
        },
        "ETHUSDT": {
            "15m": make_snapshot("ETHUSDT", taker_imbalance=0.04, funding_rate=0.0011, crowding_bias=0.12, oi_change=0.1, spread_bps=8.2, top_trader_ratio=1.5, spread_stress_score=1.62),
            "1h": make_snapshot("ETHUSDT", taker_imbalance=0.04, funding_rate=0.0011, crowding_bias=0.12, oi_change=0.1, spread_bps=8.2, top_trader_ratio=1.5, spread_stress_score=1.62).model_copy(update={"timeframe": "1h"}),
            "4h": make_snapshot("ETHUSDT", taker_imbalance=0.04, funding_rate=0.0011, crowding_bias=0.12, oi_change=0.1, spread_bps=8.2, top_trader_ratio=1.5, spread_stress_score=1.62).model_copy(update={"timeframe": "4h"}),
        },
    }

    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda symbol, **kwargs: contexts[str(symbol).upper()],
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_recent_signal_performance_summary",
        lambda self, **kwargs: {
            "score": 0.5,
            "sample_size": 0,
            "hit_rate": 0.0,
            "expectancy": 0.0,
            "net_pnl_after_fees": 0.0,
            "avg_signed_slippage_bps": 0.0,
            "loss_streak": 0,
            "underperforming": False,
            "components": {},
        },
    )
    monkeypatch.setattr(TradingOrchestrator, "_slippage_sensitivity_score", lambda self, symbol: 0.5)
    monkeypatch.setattr(TradingOrchestrator, "_confidence_consistency_score", lambda self, symbol, decision: 0.5)
    monkeypatch.setattr(TradingOrchestrator, "_candidate_exposure_impact_score", lambda self, **kwargs: 0.5)

    orchestrator = TradingOrchestrator(db_session)
    good_candidate = orchestrator._build_selection_candidate(
        symbol="BTCUSDT",
        timeframe="15m",
        upto_index=None,
        force_stale=False,
        missing_protection_symbols=set(),
        total_open_positions=0,
    )
    headwind_candidate = orchestrator._build_selection_candidate(
        symbol="ETHUSDT",
        timeframe="15m",
        upto_index=None,
        force_stale=False,
        missing_protection_symbols=set(),
        total_open_positions=0,
    )

    assert good_candidate["score"].derivatives_alignment > headwind_candidate["score"].derivatives_alignment
    assert good_candidate["score"].total_score > headwind_candidate["score"].total_score
    assert headwind_candidate["candidate"].derivatives_summary["spread_headwind"] is True
    assert headwind_candidate["candidate"].derivatives_summary["spread_stress"] is True
    assert headwind_candidate["candidate"].derivatives_summary["discount_magnitude"] > 0.0
    assert "SPREAD_HEADWIND" in headwind_candidate["candidate"].rationale_codes
    assert "TOP_TRADER_LONG_CROWDED" in headwind_candidate["candidate"].rationale_codes


def test_build_selection_candidate_discounts_alt_breakout_ahead_of_btc_eth(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    now = utcnow_naive()

    def make_snapshot(symbol: str, closes: list[float]) -> MarketSnapshotPayload:
        candles = [
            MarketCandle(
                timestamp=now - timedelta(minutes=15 * (len(closes) - index)),
                open=closes[index - 1] if index > 0 else closes[index],
                high=max(closes[index - 1] if index > 0 else closes[index], close) * 1.003,
                low=min(closes[index - 1] if index > 0 else closes[index], close) * 0.997,
                close=close,
                volume=1100.0 + (index * 35.0),
            )
            for index, close in enumerate(closes)
        ]
        return MarketSnapshotPayload(
            symbol=symbol,
            timeframe="15m",
            snapshot_time=now,
            latest_price=closes[-1],
            latest_volume=candles[-1].volume,
            candle_count=len(candles),
            is_stale=False,
            is_complete=True,
            candles=candles,
            derivatives_context=DerivativesContextPayload(
                source="binance_public",
                open_interest=150000.0,
                open_interest_change_pct=1.8,
                funding_rate=0.0001,
                taker_buy_sell_imbalance=0.18,
                perp_basis_bps=5.0,
                crowding_bias=0.08,
            ),
        )

    alt_context = {
        "15m": make_snapshot("SOLUSDT", [50.0, 50.4, 50.9, 51.5, 52.2, 53.0, 53.9, 54.9, 56.0, 57.2, 58.5, 59.9, 61.4, 63.0, 64.7, 66.5]),
        "1h": make_snapshot("SOLUSDT", [47.0, 47.5, 48.1, 48.8, 49.6, 50.5, 51.5, 52.6, 53.8, 55.1, 56.5, 58.0, 59.6, 61.3, 63.1, 65.0]).model_copy(update={"timeframe": "1h"}),
        "4h": make_snapshot("SOLUSDT", [42.0, 42.7, 43.5, 44.4, 45.4, 46.5, 47.7, 49.0, 50.4, 51.9, 53.5, 55.2, 57.0, 58.9, 60.9, 63.0]).model_copy(update={"timeframe": "4h"}),
    }
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda symbol, **kwargs: alt_context,
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_recent_signal_performance_summary",
        lambda self, **kwargs: {
            "score": 0.5,
            "sample_size": 0,
            "hit_rate": 0.0,
            "expectancy": 0.0,
            "net_pnl_after_fees": 0.0,
            "avg_signed_slippage_bps": 0.0,
            "loss_streak": 0,
            "underperforming": False,
            "components": {},
        },
    )
    monkeypatch.setattr(TradingOrchestrator, "_slippage_sensitivity_score", lambda self, symbol: 0.5)
    monkeypatch.setattr(TradingOrchestrator, "_confidence_consistency_score", lambda self, symbol, decision: 0.5)
    monkeypatch.setattr(TradingOrchestrator, "_candidate_exposure_impact_score", lambda self, **kwargs: 0.5)

    def make_lead_features(symbol: str, closes_15m: list[float], closes_1h: list[float], closes_4h: list[float]) -> FeaturePayload:
        return compute_features(
            make_snapshot(symbol, closes_15m),
            {
                "1h": make_snapshot(symbol, closes_1h).model_copy(update={"symbol": symbol, "timeframe": "1h"}),
                "4h": make_snapshot(symbol, closes_4h).model_copy(update={"symbol": symbol, "timeframe": "4h"}),
            },
        )

    confirmed_leads = {
        "BTCUSDT": make_lead_features(
            "BTCUSDT",
            [100.0, 100.6, 101.3, 102.1, 103.0, 104.0, 105.1, 106.3, 107.6, 109.0, 110.5, 112.1, 113.8, 115.6, 117.5, 119.5],
            [95.0, 95.9, 96.9, 98.0, 99.2, 100.5, 101.9, 103.4, 105.0, 106.7, 108.5, 110.4, 112.4, 114.5, 116.7, 119.0],
            [88.0, 89.7, 91.5, 93.5, 95.7, 98.1, 100.7, 103.5, 106.5, 109.7, 113.1, 116.7, 120.5, 124.5, 128.7, 133.1],
        ),
        "ETHUSDT": make_lead_features(
            "ETHUSDT",
            [80.0, 80.5, 81.1, 81.8, 82.6, 83.5, 84.5, 85.6, 86.8, 88.1, 89.5, 91.0, 92.6, 94.3, 96.1, 98.0],
            [76.0, 76.6, 77.3, 78.1, 79.0, 80.0, 81.1, 82.3, 83.6, 85.0, 86.5, 88.1, 89.8, 91.6, 93.5, 95.5],
            [70.0, 71.1, 72.3, 73.7, 75.2, 76.8, 78.6, 80.6, 82.8, 85.2, 87.8, 90.6, 93.6, 96.8, 100.2, 103.8],
        ),
    }
    for symbol in ("BTCUSDT", "ETHUSDT"):
        confirmed_leads[symbol].breakout.broke_swing_high = True
        confirmed_leads[symbol].breakout.range_breakout_direction = "up"
    lagging_leads = {
        "BTCUSDT": make_lead_features(
            "BTCUSDT",
            [100.0, 100.2, 100.5, 100.9, 101.2, 101.5, 101.8, 102.1, 102.4, 102.7, 103.0, 103.3, 103.7, 104.0, 104.4, 104.8],
            [96.0, 96.4, 96.9, 97.4, 98.0, 98.6, 99.3, 100.0, 100.8, 101.6, 102.5, 103.4, 104.4, 105.4, 106.5, 107.6],
            [90.0, 90.8, 91.7, 92.7, 93.8, 95.0, 96.3, 97.7, 99.2, 100.8, 102.5, 104.3, 106.2, 108.2, 110.3, 112.5],
        ),
        "ETHUSDT": make_lead_features(
            "ETHUSDT",
            [80.0, 80.2, 80.5, 80.8, 81.1, 81.4, 81.7, 82.0, 82.3, 82.6, 82.9, 83.2, 83.5, 83.8, 84.1, 84.4],
            [76.0, 76.3, 76.7, 77.1, 77.5, 78.0, 78.5, 79.0, 79.6, 80.2, 80.9, 81.6, 82.4, 83.2, 84.1, 85.0],
            [71.0, 71.6, 72.3, 73.1, 74.0, 75.0, 76.1, 77.3, 78.6, 80.0, 81.5, 83.1, 84.8, 86.6, 88.5, 90.5],
        ),
    }
    for symbol in ("BTCUSDT", "ETHUSDT"):
        lagging_leads[symbol].breakout.broke_swing_high = False
        lagging_leads[symbol].breakout.range_breakout_direction = "none"

    orchestrator = TradingOrchestrator(db_session)
    confirmed_candidate = orchestrator._build_selection_candidate(
        symbol="SOLUSDT",
        timeframe="15m",
        upto_index=None,
        force_stale=False,
        missing_protection_symbols=set(),
        total_open_positions=0,
        lead_market_features=confirmed_leads,
    )
    lagging_candidate = orchestrator._build_selection_candidate(
        symbol="SOLUSDT",
        timeframe="15m",
        upto_index=None,
        force_stale=False,
        missing_protection_symbols=set(),
        total_open_positions=0,
        lead_market_features=lagging_leads,
    )

    assert confirmed_candidate["score"].lead_lag_alignment > lagging_candidate["score"].lead_lag_alignment
    assert confirmed_candidate["score"].total_score > lagging_candidate["score"].total_score
    assert confirmed_candidate["candidate"].lead_lag_summary["bullish_breakout_confirmed"] is True
    assert lagging_candidate["candidate"].lead_lag_summary["bullish_breakout_ahead"] is True


def test_run_exchange_sync_cycle_calls_live_sync_and_records_success(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.flush()
    calls: list[str | None] = []

    def fake_sync_live_state(session, settings_row, *, symbol=None):
        calls.append(symbol)
        return {"symbols": [symbol or settings_row.default_symbol], "synced_positions": 1, "synced_orders": 1}

    monkeypatch.setattr("trading_mvp.services.orchestrator.sync_live_state", fake_sync_live_state)

    result = TradingOrchestrator(db_session).run_exchange_sync_cycle(symbol="BTCUSDT", trigger_event="background_poll")

    assert result["status"] == "ok"
    assert calls == ["BTCUSDT"]


def test_run_exchange_sync_cycle_records_failure_without_raising(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.flush()

    def fake_sync_live_state(session, settings_row, *, symbol=None):
        raise RuntimeError("interval failure")

    monkeypatch.setattr("trading_mvp.services.orchestrator.sync_live_state", fake_sync_live_state)

    result = TradingOrchestrator(db_session).run_exchange_sync_cycle(symbol="BTCUSDT", trigger_event="background_poll")

    assert result["status"] == "error"
    assert result["symbol"] == "BTCUSDT"
    assert "interval failure" in result["error"]


def test_run_exchange_sync_cycle_marks_scopes_skipped_when_credentials_missing(db_session) -> None:
    settings_row = get_or_create_settings(db_session)

    result = TradingOrchestrator(db_session).run_exchange_sync_cycle(symbol="BTCUSDT", trigger_event="background_poll")

    assert result["status"] == "skipped"
    assert result["reason"] == "LIVE_CREDENTIALS_MISSING"
    sync_summary = result["sync_freshness_summary"]
    assert sync_summary["account"]["status"] == "skipped"
    assert sync_summary["positions"]["status"] == "skipped"
    assert sync_summary["open_orders"]["status"] == "skipped"
    assert sync_summary["protective_orders"]["status"] == "skipped"
    assert sync_summary["account"]["last_skip_reason"] == "LIVE_CREDENTIALS_MISSING"
    assert settings_row.pause_reason_detail["exchange_sync"]["account"]["last_skip_reason"] == "LIVE_CREDENTIALS_MISSING"


def test_rank_candidate_symbols_fallback_truncates_long_errors(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()
    orchestrator = TradingOrchestrator(db_session)

    monkeypatch.setattr(
        orchestrator,
        "_sync_drawdown_state",
        lambda now=None: {
            "current_drawdown_state": "normal",
            "entered_at": None,
            "transition_reason": "stable_normal",
            "policy_adjustments": {},
        },
    )
    monkeypatch.setattr(orchestrator, "_build_lead_market_features", lambda **kwargs: {})

    def fail_candidate(**kwargs):
        raise RuntimeError("candidate failure " + ("x" * 1200))

    monkeypatch.setattr(orchestrator, "_build_selection_candidate", fail_candidate)

    result = orchestrator._rank_candidate_symbols(
        decision_symbols=["BTCUSDT"],
        timeframe="15m",
        upto_index=None,
        force_stale=False,
    )

    candidate = result["rankings"][0]["candidate"]
    assert candidate["rationale_codes"] == ["CANDIDATE_SELECTION_FALLBACK"]
    assert len(candidate["explanation_detailed"]) <= 600


def test_scheduler_exchange_sync_cycle_records_workflow(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.flush()

    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.sync_live_state",
        lambda session, settings_row, symbol=None: {"status": "ok", "symbols": ["BTCUSDT"]},
    )

    result = run_exchange_sync_cycle(db_session, triggered_by="scheduler")
    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))

    assert result["workflow"] == "exchange_sync_cycle"
    assert scheduler_run is not None
    assert scheduler_run.workflow == "exchange_sync_cycle"
    assert scheduler_run.status == "success"


def test_scheduler_exchange_sync_cycle_tags_auth_permission_failure(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.flush()

    def fail_sync_live_state(session, settings_row, *, symbol=None):  # noqa: ANN001, ARG001
        raise RuntimeError("Binance error -2015: Invalid API-key, IP, or permissions for action.")

    monkeypatch.setattr("trading_mvp.services.orchestrator.sync_live_state", fail_sync_live_state)

    result = run_exchange_sync_cycle(db_session, triggered_by="scheduler")
    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))

    assert result["workflow"] == "exchange_sync_cycle"
    assert result["status"] == "failed"
    assert result["reason_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"
    assert scheduler_run is not None
    assert scheduler_run.status == "failed"
    assert scheduler_run.outcome["reason_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"


def test_maybe_refresh_exchange_sync_freshness_runs_when_sync_is_stale(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    stale_at = utcnow_naive() - timedelta(hours=2)
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=stale_at)
    db_session.flush()

    calls: list[str] = []

    def fake_run_exchange_sync_cycle(session, triggered_by="scheduler"):
        calls.append(triggered_by)
        return {"workflow": "exchange_sync_cycle", "status": "success"}

    monkeypatch.setattr("trading_mvp.services.scheduler.run_exchange_sync_cycle", fake_run_exchange_sync_cycle)

    result = maybe_refresh_exchange_sync_freshness(db_session, triggered_by="api_dashboard_overview")

    assert result == {"workflow": "exchange_sync_cycle", "status": "success"}
    assert calls == ["api_dashboard_overview"]


def test_maybe_refresh_exchange_sync_freshness_runs_when_pre_decision_sync_is_near_stale(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    near_stale_at = utcnow_naive() - timedelta(seconds=70)
    for scope in ("account", "positions", "open_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=near_stale_at, stale_after_seconds=120)
    mark_sync_success(
        settings_row,
        scope="protective_orders",
        synced_at=near_stale_at,
        stale_after_seconds=90,
        status="flat",
    )
    db_session.flush()

    calls: list[str] = []

    def fake_run_exchange_sync_cycle(session, triggered_by="scheduler"):
        calls.append(triggered_by)
        return {"workflow": "exchange_sync_cycle", "status": "success"}

    monkeypatch.setattr("trading_mvp.services.scheduler.run_exchange_sync_cycle", fake_run_exchange_sync_cycle)

    result = maybe_refresh_exchange_sync_freshness(db_session, triggered_by="scheduler:pre_decision")

    assert result == {"workflow": "exchange_sync_cycle", "status": "success"}
    assert calls == ["scheduler:pre_decision"]


def test_maybe_refresh_exchange_sync_freshness_keeps_api_reads_from_refreshing_near_stale_sync(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    near_stale_at = utcnow_naive() - timedelta(seconds=70)
    for scope in ("account", "positions", "open_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=near_stale_at, stale_after_seconds=120)
    mark_sync_success(
        settings_row,
        scope="protective_orders",
        synced_at=near_stale_at,
        stale_after_seconds=90,
        status="flat",
    )
    db_session.flush()

    calls: list[str] = []

    def fake_run_exchange_sync_cycle(session, triggered_by="scheduler"):
        calls.append(triggered_by)
        return {"workflow": "exchange_sync_cycle", "status": "success"}

    monkeypatch.setattr("trading_mvp.services.scheduler.run_exchange_sync_cycle", fake_run_exchange_sync_cycle)

    result = maybe_refresh_exchange_sync_freshness(db_session, triggered_by="api_dashboard_overview")

    assert result is None
    assert calls == []


def test_maybe_refresh_exchange_sync_freshness_skips_api_refresh_when_paused(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.trading_paused = True
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    stale_at = utcnow_naive() - timedelta(hours=2)
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=stale_at)
    db_session.flush()

    calls: list[str] = []

    def fake_run_exchange_sync_cycle(session, triggered_by="scheduler"):
        calls.append(triggered_by)
        return {"workflow": "exchange_sync_cycle", "status": "success"}

    monkeypatch.setattr("trading_mvp.services.scheduler.run_exchange_sync_cycle", fake_run_exchange_sync_cycle)

    result = maybe_refresh_exchange_sync_freshness(db_session, triggered_by="api_dashboard_overview")

    assert result is None
    assert calls == []


def test_interval_decision_refreshes_stale_sync_before_plan(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    stale_at = utcnow_naive() - timedelta(hours=2)
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=stale_at)
    db_session.add(settings_row)
    db_session.flush()

    sync_calls: list[str] = []

    def fake_run_exchange_sync_cycle(session, triggered_by="scheduler"):
        sync_calls.append(triggered_by)
        fresh_at = utcnow_naive()
        for scope in ("account", "positions", "open_orders", "protective_orders"):
            mark_sync_success(settings_row, scope=scope, synced_at=fresh_at)
        session.add(settings_row)
        session.flush()
        return {"workflow": "exchange_sync_cycle", "status": "success", "triggered_by": triggered_by}

    selected_ranking = _ranking_candidate_payload(symbol="BTCUSDT", decision="long")
    monkeypatch.setattr("trading_mvp.services.scheduler.attempt_auto_resume", lambda *args, **kwargs: None)
    monkeypatch.setattr("trading_mvp.services.scheduler.run_exchange_sync_cycle", fake_run_exchange_sync_cycle)
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": ["BTCUSDT"],
            "skipped_symbols": [],
            "rankings": [selected_ranking],
        },
    )

    decision_invocations: list[dict[str, object]] = []

    def fake_run_decision_cycle(self, **kwargs):
        decision_invocations.append(dict(kwargs))
        return {
            "symbol": "BTCUSDT",
            "status": "completed",
            "decision_run_id": 101,
            "risk_check_id": 202,
            "decision": {"decision": "hold"},
            "risk_result": {"allowed": False, "decision": "hold", "reason_codes": ["HOLD_DECISION"]},
            "execution": None,
            "last_ai_skip_reason": None,
        }

    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    outcome = result["results"][0]["outcome"]

    assert sync_calls == ["scheduler:pre_decision"]
    assert len(decision_invocations) == 1
    assert decision_invocations[0]["exchange_sync_checked"] is True
    assert outcome["trigger"]["trigger_reason"] == "entry_candidate_event"
    assert outcome["last_ai_skip_reason"] is None
    assert outcome["pre_decision_exchange_sync"]["status"] == "success"
    assert result["pre_decision_exchange_sync"]["status"] == "success"


def test_no_event_no_ai_invocation(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    invoked_symbols: list[str] = []

    monkeypatch.setattr(
        TradingOrchestrator,
        "build_interval_decision_plan",
        lambda self, **kwargs: _interval_review_plan(symbol="BTCUSDT", trigger_reason=None),
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_decision_cycle",
        lambda self, symbol=None, **kwargs: invoked_symbols.append(str(symbol)) or {"symbol": symbol},
    )

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))

    assert invoked_symbols == []
    assert result["results"][0]["outcome"]["ai_review_status"] == "no_event"
    assert result["results"][0]["outcome"]["last_ai_skip_reason"] == "NO_EVENT"
    assert scheduler_run is not None
    assert scheduler_run.status == "success"


def test_interval_no_event_audit_persists_active_position_suppression(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    snapshot_time = utcnow_naive()
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=70120.0,
        latest_volume=1200.0,
        candle_count=3,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=2), open=70020.0, high=70080.0, low=69980.0, close=70040.0, volume=1000.0),
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=1), open=70040.0, high=70110.0, low=70010.0, close=70090.0, volume=1100.0),
            MarketCandle(timestamp=snapshot_time, open=70090.0, high=70150.0, low=70070.0, close=70120.0, volume=1200.0),
        ],
    )
    feature_payload = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.34,
        volatility_pct=0.002,
        volume_ratio=1.08,
        drawdown_pct=0.001,
        rsi=58.0,
        atr=90.0,
        atr_pct=0.0013,
        momentum_score=0.22,
        multi_timeframe={
            "15m": TimeframeFeatureContext(
                timeframe="15m",
                trend_score=0.34,
                volatility_pct=0.002,
                volume_ratio=1.08,
                drawdown_pct=0.001,
                rsi=58.0,
                atr=90.0,
                atr_pct=0.0013,
                momentum_score=0.22,
            )
        },
        regime=RegimeFeatureContext(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            volume_regime="strong",
            momentum_state="strengthening",
            weak_volume=False,
            momentum_weakening=False,
        ),
        data_quality_flags=[],
    )
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=70120.0,
            leverage=2.0,
            stop_loss=69400.0,
            take_profit=71200.0,
            realized_pnl=0.0,
            unrealized_pnl=12.0,
            metadata_json={},
        )
    )
    db_session.flush()

    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": ["BTCUSDT"],
            "skipped_symbols": [],
            "rankings": [_ranking_candidate_payload(symbol="BTCUSDT", strategy_engine="trend_pullback_engine")],
        },
    )
    monkeypatch.setattr("trading_mvp.services.orchestrator.compute_features", lambda *args, **kwargs: feature_payload)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_position_management_context",
        lambda *args, **kwargs: {
            "holding_profile": "scalp",
            "holding_profile_reason": "active_position_test",
            "hard_stop_active": True,
            "stop_widening_allowed": False,
            "current_r_multiple": 0.8,
            "break_even_eligible": True,
            "tightened_stop_loss": 70020.0,
            "reduce_reason_codes": [],
        },
    )

    class DummyGate:
        allowed = True
        reason = "allowed"

        def as_metadata(self) -> dict[str, object]:
            return {"allowed": True, "reason": "allowed"}

    monkeypatch.setattr("trading_mvp.services.orchestrator.get_openai_call_gate", lambda *args, **kwargs: DummyGate())
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_ai_prior_context",
        lambda *args, **kwargs: AIPriorContextPacket(),
    )

    def fake_run(_market_snapshot, _feature_payload, _open_positions, _risk_context, *, use_ai, **kwargs):
        return (
            TradeDecision(
                decision="long",
                confidence=0.62,
                symbol="BTCUSDT",
                timeframe="15m",
                entry_zone_min=70080.0,
                entry_zone_max=70100.0,
                entry_mode="pullback_confirm",
                invalidation_price=69400.0,
                max_chase_bps=12.0,
                idea_ttl_minutes=20,
                stop_loss=69400.0,
                take_profit=71200.0,
                max_holding_minutes=120,
                risk_pct=0.01,
                leverage=1.0,
                rationale_codes=["TEST_ADD_ON"],
                explanation_short="same-side add-on",
                explanation_detailed="Test helper tries to reuse the same-side add-on route.",
            ),
            "openai",
            {"source": "llm"},
        )

    def fake_evaluate_risk(session, settings_row, decision, market_snapshot, **kwargs):
        blocked = decision.decision == "long"
        reason_codes = ["LARGEST_POSITION_LIMIT_REACHED"] if blocked else []
        result = RiskCheckResult(
            allowed=not blocked,
            decision=decision.decision,
            reason_codes=reason_codes,
            blocked_reason_codes=reason_codes,
            adjustment_reason_codes=[],
            approved_risk_pct=0.0 if blocked else float(decision.risk_pct or 0.0),
            approved_leverage=0.0 if blocked else float(decision.leverage or 0.0),
            raw_projected_notional=0.0,
            approved_notional=0.0,
            approved_projected_notional=0.0,
            approved_qty=None,
            approved_quantity=None,
            operating_mode="hold" if blocked else "live",
            effective_leverage_cap=5.0,
            symbol_risk_tier="btc",
            exposure_metrics={},
        )
        row = RiskCheck(
            symbol=decision.symbol,
            decision_run_id=kwargs.get("decision_run_id"),
            market_snapshot_id=kwargs.get("market_snapshot_id"),
            allowed=result.allowed,
            decision=decision.decision,
            reason_codes=list(result.reason_codes),
            approved_risk_pct=result.approved_risk_pct,
            approved_leverage=result.approved_leverage,
            payload=result.model_dump(mode="json"),
        )
        session.add(row)
        session.flush()
        return result, row

    monkeypatch.setattr("trading_mvp.services.orchestrator.evaluate_risk", fake_evaluate_risk)

    orchestrator = TradingOrchestrator(db_session)
    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_run)
    orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="realtime_cycle",
        exchange_sync_checked=True,
        market_snapshot_override=snapshot,
        market_context_override={
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
        },
    )
    orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="realtime_cycle",
        exchange_sync_checked=True,
        market_snapshot_override=snapshot,
        market_context_override={
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
        },
    )

    monkeypatch.setattr(
        TradingOrchestrator,
        "run_decision_cycle",
        lambda self, **kwargs: pytest.fail("no-event interval review must not invoke decision cycle"),
    )

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    audit_event = db_session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.event_type == "decision_ai_no_event",
            AuditEvent.entity_id == "BTCUSDT",
        )
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )

    assert result["results"][0]["outcome"]["ai_review_status"] == "no_event"
    assert audit_event is not None
    assert audit_event.payload["suppression_active"] is True
    assert audit_event.payload["suppression_reason_code"] == "LARGEST_POSITION_LIMIT_REACHED"
    assert audit_event.payload["allow_same_side_add_on"] is False
    assert audit_event.payload["allowed_add_on_side"] is None


def test_run_interval_decision_cycle_marks_failure_instead_of_raising(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    def fail_cycle(self, trigger_event="realtime_cycle", **kwargs):
        raise RuntimeError("interval failure")

    monkeypatch.setattr(
        TradingOrchestrator,
        "build_interval_decision_plan",
        lambda self, **kwargs: _interval_review_plan(symbol="BTCUSDT", trigger_reason="entry_candidate_event"),
    )
    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fail_cycle)

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))

    assert result["results"][0]["outcome"]["error"] == "interval failure"
    assert scheduler_run is not None
    assert scheduler_run.status == "failed"


def test_entry_event_invokes_ai_once(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    invocation_payloads: list[dict[str, object]] = []
    selection_context = {
        "assigned_slot": "slot_1",
        "candidate_weight": 0.64,
        "slot_allocation": {
            "assigned_slot": "slot_1",
            "candidate_weight": 0.64,
            "risk_pct_multiplier": 1.0,
            "leverage_multiplier": 1.0,
            "notional_multiplier": 1.0,
            "applies_soft_limit": False,
        },
    }
    monkeypatch.setattr(
        TradingOrchestrator,
        "build_interval_decision_plan",
        lambda self, **kwargs: _interval_review_plan(
            symbol="BTCUSDT",
            trigger_reason="entry_candidate_event",
            selection_context=selection_context,
        ),
    )

    def fake_run_decision_cycle(self, symbol=None, **kwargs):
        invocation_payloads.append(
            {
                "symbol": str(symbol),
                "review_trigger": dict(kwargs.get("review_trigger") or {}),
                "selection_context": dict(kwargs.get("selection_context") or {}),
            }
        )
        return {
            "symbol": symbol,
            "status": "completed",
            "last_ai_trigger_reason": "entry_candidate_event",
            "last_ai_invoked_at": utcnow_naive().isoformat(),
            "next_ai_review_due_at": utcnow_naive().isoformat(),
            "trigger_deduped": False,
            "trigger_fingerprint": "fingerprint-1234",
            "last_ai_skip_reason": None,
        }

    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")

    assert len(invocation_payloads) == 1
    assert invocation_payloads[0]["symbol"] == "BTCUSDT"
    assert invocation_payloads[0]["review_trigger"]["trigger_reason"] == "entry_candidate_event"
    assert invocation_payloads[0]["selection_context"]["assigned_slot"] == "slot_1"
    assert result["results"][0]["outcome"]["trigger"]["trigger_reason"] == "entry_candidate_event"


def test_interval_decision_cycle_caps_new_entry_ai_dispatch_per_cycle(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    monkeypatch.setattr(
        "trading_mvp.services.scheduler._try_pre_decision_exchange_sync",
        lambda *args, **kwargs: ({"status": "fresh"}, None),
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "build_interval_decision_plan",
        lambda self, **kwargs: _interval_budget_review_plan(settings_row.tracked_symbols, breadth_regime="mixed"),
    )

    invoked_symbols: list[str] = []

    def fake_run_decision_cycle(self, symbol=None, **kwargs):
        invoked_symbols.append(str(symbol))
        return {
            "symbol": symbol,
            "status": "completed",
            "last_ai_trigger_reason": "entry_candidate_event",
            "last_ai_invoked_at": utcnow_naive().isoformat(),
            "next_ai_review_due_at": utcnow_naive().isoformat(),
            "trigger_deduped": False,
            "trigger_fingerprint": f"fingerprint-{symbol}",
            "last_ai_skip_reason": None,
        }

    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    outcomes_by_symbol = {
        item["outcome"]["symbol"]: item["outcome"]
        for item in result["results"]
    }

    assert invoked_symbols == ["BTCUSDT", "ETHUSDT"]
    assert result["ai_cycle_dispatch_budget"]["applied"] is True
    assert result["ai_cycle_dispatch_budget"]["budget"] == 2
    assert result["ai_cycle_dispatch_budget"]["allowed_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert result["ai_cycle_dispatch_budget"]["skipped_symbols"] == ["SOLUSDT", "XRPUSDT", "BNBUSDT"]
    for symbol in ["SOLUSDT", "XRPUSDT", "BNBUSDT"]:
        outcome = outcomes_by_symbol[symbol]
        assert outcome["ai_review_status"] == "skipped"
        assert outcome["last_ai_skip_reason"] == "AI_CYCLE_BUDGET_EXHAUSTED"
        assert outcome["ai_call_policy"]["skip_category"] == "cycle_dispatch_budget"
        assert outcome["ai_call_policy"]["cycle_dispatch_budget"]["budgeted_trigger_count"] == 5


def test_interval_decision_cycle_budget_preserves_protection_review(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    monkeypatch.setattr(
        "trading_mvp.services.scheduler._try_pre_decision_exchange_sync",
        lambda *args, **kwargs: ({"status": "fresh"}, None),
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "build_interval_decision_plan",
        lambda self, **kwargs: _interval_budget_review_plan(
            settings_row.tracked_symbols,
            breadth_regime="mixed",
            protection_symbol="XRPUSDT",
        ),
    )

    invoked_symbols: list[str] = []
    invoked_triggers: dict[str, str] = {}

    def fake_run_decision_cycle(self, symbol=None, **kwargs):
        review_trigger = dict(kwargs.get("review_trigger") or {})
        invoked_symbols.append(str(symbol))
        invoked_triggers[str(symbol)] = str(review_trigger.get("trigger_reason") or "")
        return {
            "symbol": symbol,
            "status": "completed",
            "last_ai_trigger_reason": review_trigger.get("trigger_reason"),
            "last_ai_invoked_at": utcnow_naive().isoformat(),
            "next_ai_review_due_at": utcnow_naive().isoformat(),
            "trigger_deduped": False,
            "trigger_fingerprint": f"fingerprint-{symbol}",
            "last_ai_skip_reason": None,
        }

    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    outcomes_by_symbol = {
        item["outcome"]["symbol"]: item["outcome"]
        for item in result["results"]
    }

    assert invoked_symbols == ["BTCUSDT", "ETHUSDT", "XRPUSDT"]
    assert invoked_triggers["XRPUSDT"] == "protection_review_event"
    assert result["ai_cycle_dispatch_budget"]["allowed_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert result["ai_cycle_dispatch_budget"]["exempt_symbols"] == ["XRPUSDT"]
    assert outcomes_by_symbol["SOLUSDT"]["last_ai_skip_reason"] == "AI_CYCLE_BUDGET_EXHAUSTED"


def _transition_watch_rejected_ranking(
    *,
    score_total: float = 0.38,
    primary_regime: str = "trend",
    trend_alignment: str = "bullish_aligned",
    volume_regime: str = "normal",
    weak_volume: bool = False,
) -> dict[str, object]:
    rejected_ranking = _ranking_candidate_payload(symbol="BTCUSDT", decision="long")
    candidate = dict(rejected_ranking["candidate"])
    candidate.update(
        {
            "rationale_codes": [
                f"REGIME_{primary_regime.upper()}",
                f"TREND_{trend_alignment.upper()}",
                "LEAD_MARKETS_NEUTRAL",
                "DERIVATIVES_NEUTRAL",
            ],
            "lead_lag_summary": {
                "leader_bias": "bullish",
                "strong_reference_confirmation": False,
                "weak_reference_confirmation": False,
                "bullish_breakout_confirmed": False,
                "bearish_breakout_confirmed": False,
                "bullish_breakout_ahead": False,
                "bearish_breakout_ahead": False,
            },
            "derivatives_summary": {
                "taker_flow_alignment": "neutral",
                "funding_bias": "neutral",
                "crowding_bias": "neutral",
                "spread_headwind": False,
                "spread_stress": False,
                "veto_reason_codes": [],
            },
        }
    )
    rejected_ranking.update(
        {
            "selected": False,
            "selected_reason": None,
            "selection_reason": "score_below_threshold",
            "rejected_reason": "score_below_threshold",
            "entry_score_threshold": 0.40,
            "regime_summary": {
                "primary_regime": primary_regime,
                "trend_alignment": trend_alignment,
                "volume_regime": volume_regime,
                "weak_volume": weak_volume,
                "momentum_weakening": False,
            },
            "candidate": candidate,
            "score": {
                "total_score": score_total,
                "lead_lag_alignment": 0.64,
                "derivatives_alignment": 0.62,
            },
        }
    )
    return rejected_ranking


def _transition_watch_selection_context(ranking: dict[str, object]) -> dict[str, object]:
    return {
        "breadth_regime": "mixed",
        "capacity_reason": "mixed_breadth_moderate_capacity",
        "strategy_engine": ranking.get("strategy_engine"),
        "holding_profile": ranking.get("holding_profile"),
        "entry_mode": ranking.get("entry_mode"),
        "scenario": dict(ranking.get("candidate") or {}).get("scenario"),
        "slot_conviction_score": ranking.get("slot_conviction_score"),
        "meta_gate_probability": ranking.get("meta_gate_probability"),
        "entry_score_threshold": ranking.get("entry_score_threshold"),
        "regime_summary": dict(ranking.get("regime_summary") or {}),
        "candidate": dict(ranking.get("candidate") or {}),
        "score": dict(ranking.get("score") or {}),
    }


def test_interval_plan_soft_score_reject_suppresses_weak_ai_review_trigger(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    rejected_ranking = _ranking_candidate_payload(symbol="BTCUSDT", decision="long")
    rejected_ranking.update(
        {
            "selected": False,
            "selected_reason": None,
            "selection_reason": "score_below_threshold",
            "rejected_reason": "score_below_threshold",
            "entry_score_threshold": 0.40,
            "score": {"total_score": 0.30},
        }
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": [],
            "skipped_symbols": ["BTCUSDT"],
            "rankings": [rejected_ranking],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"] is None
    assert symbol_plan["last_ai_skip_reason"] == "SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE"
    assert symbol_plan["ai_call_policy"]["ai_call_event"] == "AI_CALL_SKIPPED"
    assert symbol_plan["ai_call_policy"]["reason"] == "soft_signal_review_suppressed_weak_candidate"
    assert symbol_plan["ai_call_policy"]["hard_skip_ai"] is False
    assert symbol_plan["ai_call_policy"]["skip_category"] == "cost_saved_weak_candidate"
    assert symbol_plan["ai_call_policy"]["allow_ai_but_later_risk_check"] == ["SOFT_SIGNAL_AI_REVIEW"]
    assert symbol_plan["ai_call_policy"]["threshold_gap"] == pytest.approx(0.10)
    assert symbol_plan["selection_context"]["ai_call_policy"]["reason"] == (
        "soft_signal_review_suppressed_weak_candidate"
    )


def test_interval_plan_near_threshold_soft_reject_allows_transition_watch(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    rejected_ranking = _ranking_candidate_payload(symbol="BTCUSDT", decision="long")
    rejected_ranking.update(
        {
            "selected": False,
            "selected_reason": None,
            "selection_reason": "score_below_threshold",
            "rejected_reason": "score_below_threshold",
            "entry_score_threshold": 0.40,
            "score": {"total_score": 0.38},
        }
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": [],
            "skipped_symbols": ["BTCUSDT"],
            "rankings": [rejected_ranking],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"] is not None
    assert symbol_plan["last_ai_skip_reason"] is None
    assert symbol_plan["trigger"]["forced_review_reason"] == "soft_signal_transition_watch_due"
    assert "SOFT_SIGNAL_TRANSITION_WATCH" in symbol_plan["trigger"]["reason_codes"]
    assert symbol_plan["ai_call_policy"]["ai_call_event"] == "AI_CALL_ALLOWED"
    assert symbol_plan["ai_call_policy"]["soft_signal_review_mode"] == "transition_watch"
    assert symbol_plan["ai_call_policy"]["transition_watch_ai_invoked"] is True
    assert symbol_plan["ai_call_policy"]["threshold_gap"] == pytest.approx(0.02)


def test_interval_plan_low_edge_hold_candidate_suppresses_transition_watch(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    rejected_ranking = _transition_watch_rejected_ranking(score_total=0.41)
    candidate = dict(rejected_ranking["candidate"])
    candidate.update(
        {
            "decision": "hold",
            "entry_zone_min": None,
            "entry_zone_max": None,
            "stop_loss": None,
            "take_profit": None,
        }
    )
    rejected_ranking.update(
        {
            "selection_reason": "low_edge_hold_candidate",
            "rejected_reason": "low_edge_hold_candidate",
            "candidate": candidate,
            "slot_conviction_score": 0.54,
            "meta_gate_probability": 0.50,
            "agreement_alignment_score": 0.52,
            "agreement_level_hint": "disagreement_risk",
        }
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": [],
            "skipped_symbols": ["BTCUSDT"],
            "rankings": [rejected_ranking],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"] is None
    assert symbol_plan["last_ai_skip_reason"] == "SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE"
    assert symbol_plan["ai_call_policy"]["ai_call_event"] == "AI_CALL_SKIPPED"
    assert symbol_plan["ai_call_policy"]["skip_category"] == "cost_saved_low_edge_hold_candidate"
    assert symbol_plan["ai_call_policy"]["soft_signal_review_mode"] == "cost_saved_low_edge_hold_candidate"
    assert symbol_plan["ai_call_policy"]["low_edge_hold_suppression"]["active"] is True
    assert symbol_plan["ai_call_policy"]["low_edge_hold_suppression"]["weak_quality"] is True


def test_interval_plan_low_edge_hold_candidate_keeps_actionable_quality_exception(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    rejected_ranking = _transition_watch_rejected_ranking(score_total=0.41)
    candidate = dict(rejected_ranking["candidate"])
    candidate.update(
        {
            "decision": "hold",
            "entry_zone_min": None,
            "entry_zone_max": None,
            "stop_loss": None,
            "take_profit": None,
        }
    )
    rejected_ranking.update(
        {
            "selection_reason": "low_edge_hold_candidate",
            "rejected_reason": "low_edge_hold_candidate",
            "candidate": candidate,
            "slot_conviction_score": 0.63,
            "meta_gate_probability": 0.60,
            "agreement_alignment_score": 0.52,
            "agreement_level_hint": "disagreement_risk",
        }
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": [],
            "skipped_symbols": ["BTCUSDT"],
            "rankings": [rejected_ranking],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"] is not None
    assert symbol_plan["last_ai_skip_reason"] is None
    assert symbol_plan["ai_call_policy"]["ai_call_event"] == "AI_CALL_ALLOWED"
    assert symbol_plan["ai_call_policy"]["soft_signal_review_mode"] == "transition_watch"


def test_interval_plan_soft_score_reject_suppression_takes_precedence_after_recent_ai(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.ai_call_interval_minutes = 5
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    now = utcnow_naive()
    db_session.add(
        AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="openai",
            summary="prior soft hold",
            input_payload={
                "market_snapshot": {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "snapshot_time": (now - timedelta(minutes=5)).isoformat(),
                }
            },
            output_payload={"symbol": "BTCUSDT", "timeframe": "15m", "decision": "hold"},
            metadata_json={"symbol": "BTCUSDT", "timeframe": "15m", "source": "llm"},
            schema_valid=True,
            started_at=now - timedelta(minutes=5),
            completed_at=now - timedelta(minutes=5),
            created_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=5),
        )
    )
    db_session.flush()

    rejected_ranking = _ranking_candidate_payload(symbol="BTCUSDT", decision="long")
    rejected_ranking.update(
        {
            "selected": False,
            "selected_reason": None,
            "selection_reason": "score_below_threshold",
            "rejected_reason": "score_below_threshold",
        }
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": [],
            "skipped_symbols": ["BTCUSDT"],
            "rankings": [rejected_ranking],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"] is None
    assert symbol_plan["last_ai_skip_reason"] == "SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE"
    assert symbol_plan["ai_call_policy"]["ai_call_event"] == "AI_CALL_SKIPPED"
    assert symbol_plan["ai_call_policy"]["reason"] == "soft_signal_review_suppressed_weak_candidate"
    assert symbol_plan["ai_call_policy"]["hard_skip_ai"] is False
    assert "cooldown_minutes" not in symbol_plan["ai_call_policy"]


def test_interval_plan_near_threshold_soft_reject_cooldown_after_recent_ai(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.ai_call_interval_minutes = 5
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    now = utcnow_naive()
    db_session.add(
        AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="openai",
            summary="prior transition watch",
            input_payload={
                "market_snapshot": {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "snapshot_time": (now - timedelta(minutes=5)).isoformat(),
                }
            },
            output_payload={"symbol": "BTCUSDT", "timeframe": "15m", "decision": "hold"},
            metadata_json={
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "source": "llm",
                "soft_signal_review_mode": "transition_watch",
            },
            schema_valid=True,
            started_at=now - timedelta(minutes=5),
            completed_at=now - timedelta(minutes=5),
            created_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=5),
        )
    )
    db_session.flush()

    rejected_ranking = _ranking_candidate_payload(symbol="BTCUSDT", decision="long")
    rejected_ranking.update(
        {
            "selected": False,
            "selected_reason": None,
            "selection_reason": "score_below_threshold",
            "rejected_reason": "score_below_threshold",
            "entry_score_threshold": 0.40,
            "score": {"total_score": 0.38},
        }
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": [],
            "skipped_symbols": ["BTCUSDT"],
            "rankings": [rejected_ranking],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"] is None
    assert symbol_plan["last_ai_skip_reason"] == "SOFT_SIGNAL_REVIEW_COOLDOWN_ACTIVE"
    assert symbol_plan["ai_call_policy"]["ai_call_event"] == "AI_CALL_SKIPPED"
    assert symbol_plan["ai_call_policy"]["reason"] == "soft_signal_review_cooldown_active"
    assert symbol_plan["ai_call_policy"]["skip_category"] == "transition_watch_cooldown"
    assert symbol_plan["ai_call_policy"]["cooldown_minutes"] == 60


def test_interval_plan_near_threshold_soft_reject_uses_hold_backoff_after_repeated_holds(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.ai_call_interval_minutes = 5
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    now = utcnow_naive()
    for index, minutes_ago in enumerate((10, 80, 150), start=1):
        decision_row = AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="openai",
            summary=f"prior transition watch hold {index}",
            input_payload={
                "market_snapshot": {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "snapshot_time": (now - timedelta(minutes=minutes_ago)).isoformat(),
                }
            },
            output_payload={"symbol": "BTCUSDT", "timeframe": "15m", "decision": "hold"},
            metadata_json={
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "source": "llm",
                "soft_signal_review_mode": "transition_watch",
                "allow_ai_but_later_risk_check": [
                    "SOFT_SIGNAL_AI_REVIEW",
                    "SOFT_SIGNAL_TRANSITION_WATCH",
                ],
            },
            schema_valid=True,
            started_at=now - timedelta(minutes=minutes_ago),
            completed_at=now - timedelta(minutes=minutes_ago),
            created_at=now - timedelta(minutes=minutes_ago),
            updated_at=now - timedelta(minutes=minutes_ago),
        )
        db_session.add(decision_row)
        db_session.flush()
        db_session.add(
            RiskCheck(
                symbol="BTCUSDT",
                decision_run_id=decision_row.id,
                market_snapshot_id=None,
                allowed=False,
                decision="hold",
                reason_codes=["HOLD_DECISION"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={
                    "allowed": False,
                    "decision": "hold",
                    "reason_codes": ["HOLD_DECISION"],
                    "blocked_reason_codes": ["HOLD_DECISION"],
                },
            )
        )
    db_session.flush()

    rejected_ranking = _transition_watch_rejected_ranking(score_total=0.39)
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": [],
            "skipped_symbols": ["BTCUSDT"],
            "rankings": [rejected_ranking],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"] is None
    assert symbol_plan["last_ai_skip_reason"] == "SOFT_SIGNAL_REVIEW_HOLD_BACKOFF_ACTIVE"
    assert symbol_plan["ai_call_policy"]["ai_call_event"] == "AI_CALL_SKIPPED"
    assert symbol_plan["ai_call_policy"]["reason"] == "soft_signal_review_hold_backoff_active"
    assert symbol_plan["ai_call_policy"]["skip_category"] == "transition_watch_hold_backoff"
    assert symbol_plan["ai_call_policy"]["adaptive_hold_backoff"]["sample_count"] == 3


def test_interval_plan_material_change_overrides_soft_signal_hold_backoff(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.ai_call_interval_minutes = 5
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    now = utcnow_naive()
    previous_ranking = _transition_watch_rejected_ranking(score_total=0.39)
    previous_selection_context = _transition_watch_selection_context(previous_ranking)
    previous_snapshot = TradingOrchestrator._soft_signal_review_snapshot(previous_selection_context)
    for index, minutes_ago in enumerate((70, 140, 210), start=1):
        decision_row = AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="openai",
            summary=f"prior transition watch hold {index}",
            input_payload={
                "market_snapshot": {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "snapshot_time": (now - timedelta(minutes=minutes_ago)).isoformat(),
                }
            },
            output_payload={"symbol": "BTCUSDT", "timeframe": "15m", "decision": "hold"},
            metadata_json={
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "source": "llm",
                "soft_signal_review_mode": "transition_watch",
                "selection_context": previous_selection_context,
                "soft_signal_review_snapshot": previous_snapshot,
                "allow_ai_but_later_risk_check": [
                    "SOFT_SIGNAL_AI_REVIEW",
                    "SOFT_SIGNAL_TRANSITION_WATCH",
                ],
            },
            schema_valid=True,
            started_at=now - timedelta(minutes=minutes_ago),
            completed_at=now - timedelta(minutes=minutes_ago),
            created_at=now - timedelta(minutes=minutes_ago),
            updated_at=now - timedelta(minutes=minutes_ago),
        )
        db_session.add(decision_row)
        db_session.flush()
        db_session.add(
            RiskCheck(
                symbol="BTCUSDT",
                decision_run_id=decision_row.id,
                market_snapshot_id=None,
                allowed=False,
                decision="hold",
                reason_codes=["HOLD_DECISION"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={
                    "allowed": False,
                    "decision": "hold",
                    "reason_codes": ["HOLD_DECISION"],
                    "blocked_reason_codes": ["HOLD_DECISION"],
                },
            )
        )
    db_session.flush()

    current_ranking = _transition_watch_rejected_ranking(
        score_total=0.39,
        primary_regime="range",
        trend_alignment="range",
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": [],
            "skipped_symbols": ["BTCUSDT"],
            "rankings": [current_ranking],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"] is not None
    assert symbol_plan["last_ai_skip_reason"] is None
    assert symbol_plan["ai_call_policy"]["ai_call_event"] == "AI_CALL_ALLOWED"
    assert symbol_plan["ai_call_policy"]["adaptive_hold_backoff_override"]["active"] is True
    assert symbol_plan["ai_call_policy"]["adaptive_hold_backoff_override"]["reason"] == "material_actionable_change"
    assert symbol_plan["ai_call_policy"]["adaptive_hold_backoff_override"]["adaptive_hold_backoff"]["sample_count"] == 3


def test_interval_plan_selected_neutral_entry_uses_hold_backoff_after_repeated_holds(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.ai_call_interval_minutes = 5
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    now = utcnow_naive()
    neutral_reason_codes = [
        "ENGINE_TREND_PULLBACK_ENGINE",
        "REGIME_TRANSITION",
        "TREND_MIXED",
        "EXPECTANCY_NEUTRAL",
        "DERIVATIVES_NEUTRAL",
        "LEAD_MARKETS_NEUTRAL",
        "PROVIDER_OPENAI",
        "HOLDING_PROFILE_SCALP_DEFAULT",
    ]
    for index, minutes_ago in enumerate((10, 80, 150), start=1):
        decision_row = AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="openai",
            summary=f"prior neutral entry hold {index}",
            input_payload={
                "market_snapshot": {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "snapshot_time": (now - timedelta(minutes=minutes_ago)).isoformat(),
                }
            },
            output_payload={
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "decision": "hold",
                "rationale_codes": neutral_reason_codes,
            },
            metadata_json={
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "source": "llm",
            },
            schema_valid=True,
            started_at=now - timedelta(minutes=minutes_ago),
            completed_at=now - timedelta(minutes=minutes_ago),
            created_at=now - timedelta(minutes=minutes_ago),
            updated_at=now - timedelta(minutes=minutes_ago),
        )
        db_session.add(decision_row)
        db_session.flush()
        db_session.add(
            RiskCheck(
                symbol="BTCUSDT",
                decision_run_id=decision_row.id,
                market_snapshot_id=None,
                allowed=False,
                decision="hold",
                reason_codes=["HOLD_DECISION"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={
                    "allowed": False,
                    "decision": "hold",
                    "reason_codes": ["HOLD_DECISION"],
                    "blocked_reason_codes": ["HOLD_DECISION"],
                },
            )
        )
    db_session.flush()

    ranking = _ranking_candidate_payload(symbol="BTCUSDT", decision="long")
    candidate = dict(ranking["candidate"])
    candidate["rationale_codes"] = [
        "ENGINE_TREND_PULLBACK_ENGINE",
        "REGIME_TRANSITION",
        "TREND_MIXED",
        "EXPECTANCY_NEUTRAL",
        "DERIVATIVES_NEUTRAL",
        "LEAD_MARKETS_NEUTRAL",
    ]
    ranking["candidate"] = candidate
    ranking["score"] = {"total_score": 0.62, "lead_lag_alignment": 0.60, "derivatives_alignment": 0.60}
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": ["BTCUSDT"],
            "skipped_symbols": [],
            "rankings": [ranking],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"] is None
    assert symbol_plan["last_ai_skip_reason"] == "ENTRY_CANDIDATE_NEUTRAL_CONTEXT_HOLD_BACKOFF"
    assert symbol_plan["ai_call_policy"]["ai_call_event"] == "AI_CALL_SKIPPED"
    assert symbol_plan["ai_call_policy"]["reason"] == "neutral_entry_review_hold_backoff_active"
    assert symbol_plan["ai_call_policy"]["skip_category"] == "neutral_entry_hold_backoff"
    assert symbol_plan["ai_call_policy"]["neutral_entry_hold_backoff"]["sample_count"] == 3


def test_interval_plan_transition_watch_skips_without_material_change(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.ai_call_interval_minutes = 5
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    now = utcnow_naive()
    rejected_ranking = _transition_watch_rejected_ranking(score_total=0.38)
    previous_selection_context = _transition_watch_selection_context(rejected_ranking)
    db_session.add(
        AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="openai",
            summary="prior transition watch",
            input_payload={
                "market_snapshot": {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "snapshot_time": (now - timedelta(minutes=65)).isoformat(),
                }
            },
            output_payload={"symbol": "BTCUSDT", "timeframe": "15m", "decision": "hold"},
            metadata_json={
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "source": "llm",
                "soft_signal_review_mode": "transition_watch",
                "selection_context": previous_selection_context,
                "soft_signal_review_snapshot": TradingOrchestrator._soft_signal_review_snapshot(
                    previous_selection_context
                ),
            },
            schema_valid=True,
            started_at=now - timedelta(minutes=65),
            completed_at=now - timedelta(minutes=65),
            created_at=now - timedelta(minutes=65),
            updated_at=now - timedelta(minutes=65),
        )
    )
    db_session.flush()

    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": [],
            "skipped_symbols": ["BTCUSDT"],
            "rankings": [rejected_ranking],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"] is None
    assert symbol_plan["last_ai_skip_reason"] == "SOFT_SIGNAL_REVIEW_NO_MATERIAL_CHANGE"
    assert symbol_plan["ai_call_policy"]["ai_call_event"] == "AI_CALL_SKIPPED"
    assert symbol_plan["ai_call_policy"]["reason"] == "soft_signal_review_no_material_change"
    assert symbol_plan["ai_call_policy"]["skip_category"] == "transition_watch_no_material_change"
    material_change = symbol_plan["ai_call_policy"]["soft_signal_review_material_change"]
    assert material_change["material_changed"] is False
    assert material_change["changed_fields"] == []


def test_interval_plan_transition_watch_allows_material_change_after_cooldown(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.ai_call_interval_minutes = 5
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    now = utcnow_naive()
    previous_ranking = _transition_watch_rejected_ranking(score_total=0.38)
    previous_selection_context = _transition_watch_selection_context(previous_ranking)
    db_session.add(
        AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="openai",
            summary="prior transition watch",
            input_payload={
                "market_snapshot": {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "snapshot_time": (now - timedelta(minutes=65)).isoformat(),
                }
            },
            output_payload={"symbol": "BTCUSDT", "timeframe": "15m", "decision": "hold"},
            metadata_json={
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "source": "llm",
                "soft_signal_review_mode": "transition_watch",
                "selection_context": previous_selection_context,
                "soft_signal_review_snapshot": TradingOrchestrator._soft_signal_review_snapshot(
                    previous_selection_context
                ),
            },
            schema_valid=True,
            started_at=now - timedelta(minutes=65),
            completed_at=now - timedelta(minutes=65),
            created_at=now - timedelta(minutes=65),
            updated_at=now - timedelta(minutes=65),
        )
    )
    db_session.flush()

    current_ranking = _transition_watch_rejected_ranking(
        score_total=0.38,
        primary_regime="range",
        trend_alignment="range",
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": [],
            "skipped_symbols": ["BTCUSDT"],
            "rankings": [current_ranking],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"] is not None
    assert symbol_plan["last_ai_skip_reason"] is None
    assert symbol_plan["ai_call_policy"]["ai_call_event"] == "AI_CALL_ALLOWED"
    material_change = symbol_plan["ai_call_policy"]["soft_signal_review_material_change"]
    assert material_change["material_changed"] is True
    assert "primary_regime" in material_change["changed_fields"]
    assert "trend_alignment" in material_change["changed_fields"]


def test_interval_plan_account_untrusted_suppresses_new_entry_trigger(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    selected_ranking = _ranking_candidate_payload(symbol="BTCUSDT", decision="long")
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": ["BTCUSDT"],
            "skipped_symbols": [],
            "rankings": [selected_ranking],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"] is None
    assert symbol_plan["last_ai_skip_reason"] == "account_untrusted"
    assert symbol_plan["ai_call_policy"]["ai_call_event"] == "AI_CALL_SKIPPED"
    assert symbol_plan["ai_call_policy"]["hard_skip_ai"] is True
    assert symbol_plan["ai_call_policy"]["reason"] == "account_untrusted"


def test_interval_cycle_account_untrusted_skips_before_decision_cycle(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    selected_ranking = _ranking_candidate_payload(symbol="BTCUSDT", decision="long")
    monkeypatch.setattr("trading_mvp.services.scheduler.attempt_auto_resume", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": ["BTCUSDT"],
            "skipped_symbols": [],
            "rankings": [selected_ranking],
        },
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_decision_cycle",
        lambda self, **kwargs: pytest.fail("account_untrusted interval plan must not create AgentRun"),
    )

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    audit = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "decision_ai_skipped", AuditEvent.entity_id == "BTCUSDT")
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )

    assert result["results"][0]["outcome"]["ai_review_status"] == "skipped"
    assert result["results"][0]["outcome"]["last_ai_skip_reason"] == "account_untrusted"
    assert db_session.scalar(select(AgentRun).limit(1)) is None
    assert audit is not None
    assert audit.payload["reason"] == "account_untrusted"
    assert audit.payload["ai_call_event"] == "AI_CALL_SKIPPED"
    assert audit.payload["hard_skip_ai"] is True


def _run_pre_ai_gate_decision(
    monkeypatch,
    db_session,
    *,
    trigger_reason: str,
    feature_payload: FeaturePayload,
    provider,
    advisor_provider=None,
    open_position: Position | None = None,
    selection_context: dict[str, object] | None = None,
    snapshot: MarketSnapshotPayload | None = None,
    sync_fresh: bool = True,
    settings_mutator=None,
    auto_resume_checked: bool = False,
    seed_advisor_run: bool = False,
):
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    if sync_fresh:
        _mark_pipeline_sync_fresh(settings_row)
    if settings_mutator is not None:
        settings_mutator(settings_row)
    db_session.add(settings_row)
    if open_position is not None:
        db_session.add(open_position)
    db_session.flush()

    snapshot = snapshot or _pre_ai_gate_snapshot()
    trigger = _interval_review_plan(trigger_reason=trigger_reason)["plans"][0]["trigger"]
    monkeypatch.setattr("trading_mvp.services.orchestrator.compute_features", lambda *args, **kwargs: feature_payload)
    monkeypatch.setattr("trading_mvp.services.orchestrator.get_openai_call_gate", lambda *args, **kwargs: _AllowAiGate())

    orchestrator = TradingOrchestrator(db_session)
    orchestrator.trading_agent = TradingDecisionAgent(provider)
    if advisor_provider is not None:
        orchestrator.market_settings_advisor = MarketSettingsAdvisorAgent(advisor_provider)
    if seed_advisor_run:
        seeded_result = orchestrator._maybe_run_market_settings_advisor(
            symbol="BTCUSDT",
            trigger_event="realtime_cycle",
            market_snapshot=snapshot,
            feature_payload=feature_payload,
            runtime_state=summarize_runtime_state(settings_row),
            cycle_id="seed-advisor-before-trading-ai",
            snapshot_id=0,
            force=True,
        )
        assert seeded_result is not None
    result = orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="manual" if trigger_reason == "manual_review_event" else "realtime_cycle",
        auto_resume_checked=auto_resume_checked,
        exchange_sync_checked=True,
        market_snapshot_override=snapshot,
        market_context_override={
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
        },
        selection_context=selection_context or {"assigned_slot": "slot_1", "candidate_weight": 0.64},
        review_trigger=trigger,
    )
    decision_row = db_session.get(AgentRun, result["decision_run_id"])
    assert decision_row is not None
    return result, decision_row


def test_trading_ai_refreshes_profile_advisor_before_provider_call(monkeypatch, db_session) -> None:
    provider = _RoutingAdvisorDecisionProvider(decision="hold", advisor_profile="CAUTION")

    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
        advisor_provider=provider,
    )
    trading_payload = provider.trading_payloads[0]
    risk_context = trading_payload["risk_context"]
    ai_context = trading_payload["ai_context"]
    advisor_context = risk_context["execution_risk_profile_advisor"]

    assert provider.advisor_calls == 1
    assert provider.trading_calls == 1
    assert result["market_settings_advisor"]["status"] == "shadow_generated"
    assert advisor_context["recommendation"]["recommended_profile_id"] == "CAUTION"
    assert advisor_context["recommendation"]["suggested_new_entry_policy"] == "STRICT_CONFIRMATION_ONLY"
    assert ai_context["execution_constraints_summary"]["execution_risk_profile_advisor"] == advisor_context
    assert decision_row.metadata_json["market_settings_advisor_pre_trade_refresh"] is True
    assert decision_row.metadata_json["market_settings_advisor_context"] == advisor_context


def test_trading_ai_reuses_fresh_profile_advisor_before_provider_call(monkeypatch, db_session) -> None:
    provider = _RoutingAdvisorDecisionProvider(decision="hold", advisor_profile="CAUTION")

    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
        advisor_provider=provider,
        seed_advisor_run=True,
    )
    trading_payload = provider.trading_payloads[0]
    advisor_context = trading_payload["risk_context"]["execution_risk_profile_advisor"]

    assert provider.advisor_calls == 1
    assert provider.trading_calls == 1
    assert result["market_settings_advisor"]["status"] == "reused"
    assert result["market_settings_advisor"]["skip_reason"] == "advisor_recommendation_reused"
    assert advisor_context["status"] == "reused"
    assert advisor_context["recommendation"]["recommended_profile_id"] == "CAUTION"
    assert advisor_context["reuse_ttl_seconds"] == 900
    assert decision_row.metadata_json["market_settings_advisor_pre_trade_refresh"] is False
    assert decision_row.metadata_json["market_settings_advisor_pre_trade_status"] == "reused"
    assert decision_row.metadata_json["market_settings_advisor_context"] == advisor_context


def test_profile_advisor_not_called_when_trading_ai_is_pre_ai_skipped(monkeypatch, db_session) -> None:
    provider = _RoutingAdvisorDecisionProvider(decision="hold", advisor_profile="CAUTION")

    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
        advisor_provider=provider,
        sync_fresh=False,
    )

    assert provider.advisor_calls == 0
    assert provider.trading_calls == 0
    assert result["market_settings_advisor"] is None
    assert result["last_ai_skip_reason"] == "account_untrusted"
    assert decision_row.metadata_json["market_settings_advisor_pre_trade_refresh"] is False
    assert decision_row.metadata_json["market_settings_advisor_context"] is None


def test_entry_candidate_weak_volume_allows_ai_and_records_soft_context(monkeypatch, db_session) -> None:
    provider = _CountingDecisionProvider(decision="hold")
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=0.07, weak_volume=True, volume_regime="weak"),
        provider=provider,
    )
    invoked_audit = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "decision_ai_invoked", AuditEvent.entity_id == str(decision_row.id))
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    skipped_audit = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "decision_ai_skipped", AuditEvent.entity_id == str(decision_row.id))
        .limit(1)
    )

    assert provider.calls == 1
    assert result["decision"]["decision"] == "hold"
    assert result["last_ai_trigger_reason"] == "entry_candidate_event"
    assert result["last_ai_invoked_at"] is not None
    assert result["last_ai_skip_reason"] is None
    assert result["risk_check_id"] is not None
    assert "HOLD_DECISION" in result["risk_result"]["reason_codes"]
    assert result["execution"] is None
    assert decision_row.provider_name == "openai"
    assert decision_row.metadata_json["last_ai_skip_reason"] is None
    assert decision_row.metadata_json["pre_ai_skip_reason"] is None
    assert decision_row.metadata_json["hard_skip_ai"] is False
    assert "ENTRY_CANDIDATE_WEAK_VOLUME_CONTEXT" in decision_row.metadata_json["allow_ai_but_later_risk_check"]
    assert invoked_audit is not None
    assert invoked_audit.payload["ai_call_event"] == "AI_CALL_ALLOWED"
    assert "ENTRY_CANDIDATE_WEAK_VOLUME_CONTEXT" in invoked_audit.payload["allow_ai_but_later_risk_check"]
    assert skipped_audit is None
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_entry_candidate_hold_watch_entry_plan_arms_pending_plan(monkeypatch, db_session) -> None:
    class WatchPlanProvider(_CountingDecisionProvider):
        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            result = super().generate(role, payload, response_model=response_model, instructions=instructions)
            result.output["watch_entry_plan"] = {
                "side": "long",
                "entry_zone_min": 69800.0,
                "entry_zone_max": 69950.0,
                "entry_mode": "pullback_confirm",
                "invalidation_price": 69400.0,
                "max_chase_bps": 12.0,
                "idea_ttl_minutes": 18,
                "stop_loss": 69400.0,
                "take_profit": 71000.0,
                "reason_codes": ["WATCH_SUPPORT_RECLAIM"],
            }
            result.output["psychology_scene_review"] = {
                "market_psychology": "Pullback buyers are waiting while chase risk remains elevated.",
                "psychology_bias": "bullish",
                "scene_scenario": "Trend pullback needs a zone touch before a new entry.",
                "scene_type": "trend_pullback",
                "entry_choreography": "Arm the zone, then require watcher confirmation before execution.",
                "preferred_entry_timing": "watch_zone",
                "confirmation_cues": ["zone_touch", "volume_reclaim"],
                "invalidation_cues": ["support_lost"],
                "reason_codes": ["SCENE_TREND_PULLBACK"],
                "summary": "Scene metadata must follow the pending plan without executing.",
                "execution_boundary": "metadata_only_no_order_authority",
            }
            return result

    def fail_execute(*args, **kwargs):
        raise AssertionError("watch entry plan must wait for watcher recheck before execution")

    monkeypatch.setattr("trading_mvp.services.orchestrator.evaluate_risk", _entry_plan_fake_evaluate_risk)
    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    provider = WatchPlanProvider(decision="hold")
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
    )
    pending_plan = db_session.scalar(select(PendingEntryPlan).limit(1))
    watch_risk = db_session.scalar(
        select(RiskCheck)
        .where(RiskCheck.decision_run_id == decision_row.id, RiskCheck.decision == "long")
        .limit(1)
    )
    risk_blocked = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "decision_risk_blocked", AuditEvent.entity_id == str(decision_row.id))
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )

    assert provider.calls == 1
    assert result["decision"]["decision"] == "hold"
    assert result["status"] == "entry_plan_armed"
    assert result["entry_plan"]["side"] == "long"
    assert result["entry_plan"]["entry_zone_min"] == 69800.0
    assert result["entry_plan"]["entry_zone_max"] == 69950.0
    assert pending_plan is not None
    assert pending_plan.plan_status == "armed"
    assert "AI_WATCH_ENTRY_PLAN" in pending_plan.rationale_codes
    assert pending_plan.source_decision_run_id == decision_row.id
    assert pending_plan.metadata_json["psychology_scene_review"]["scene_type"] == "trend_pullback"
    assert pending_plan.metadata_json["psychology_scene_review"]["execution_boundary"] == "metadata_only_no_order_authority"
    assert watch_risk is not None
    assert watch_risk.allowed is False
    assert "ENTRY_TRIGGER_NOT_MET" in watch_risk.reason_codes
    assert risk_blocked is not None
    assert risk_blocked.payload["intent"] == "watch_entry_plan"
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_abstained_hold_watch_entry_plan_still_arms_pending_plan(monkeypatch, db_session) -> None:
    class WatchPlanProvider(_CountingDecisionProvider):
        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            result = super().generate(role, payload, response_model=response_model, instructions=instructions)
            result.output["should_abstain"] = True
            result.output["abstain_reason_codes"] = ["LOW_CONVICTION_DIRECT_ENTRY"]
            result.output["watch_entry_plan"] = {
                "side": "long",
                "entry_zone_min": 69800.0,
                "entry_zone_max": 69950.0,
                "entry_mode": "pullback_confirm",
                "invalidation_price": 69400.0,
                "max_chase_bps": 12.0,
                "idea_ttl_minutes": 18,
                "stop_loss": 69400.0,
                "take_profit": 71000.0,
                "reason_codes": ["WATCH_SUPPORT_RECLAIM"],
            }
            return result

    def fail_execute(*args, **kwargs):
        raise AssertionError("abstained watch entry plan must wait for watcher recheck before execution")

    monkeypatch.setattr("trading_mvp.services.orchestrator.evaluate_risk", _entry_plan_fake_evaluate_risk)
    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    provider = WatchPlanProvider(decision="hold")
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
    )
    pending_plan = db_session.scalar(select(PendingEntryPlan).limit(1))
    watch_risk = db_session.scalar(
        select(RiskCheck)
        .where(RiskCheck.decision_run_id == decision_row.id, RiskCheck.decision == "long")
        .limit(1)
    )

    assert result["decision"]["decision"] == "hold"
    assert result["decision"]["should_abstain"] is True
    assert result["status"] == "entry_plan_armed"
    assert pending_plan is not None
    assert pending_plan.plan_status == "armed"
    assert "AI_WATCH_ENTRY_PLAN" in pending_plan.rationale_codes
    assert "LOW_CONVICTION_DIRECT_ENTRY" in pending_plan.metadata_json["source_no_trade_reason_codes"]
    assert watch_risk is not None
    assert watch_risk.allowed is False
    assert "ENTRY_TRIGGER_NOT_MET" in watch_risk.reason_codes
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_due_entry_plan_watcher_cleans_stale_triggered_history_without_armed_plan(db_session) -> None:
    stale_plan = PendingEntryPlan(
        symbol="BTCUSDT",
        side="long",
        plan_status="triggered",
        source_decision_run_id=9301,
        source_timeframe="15m",
        entry_mode="pullback_confirm",
        entry_zone_min=69900.0,
        entry_zone_max=70000.0,
        invalidation_price=69500.0,
        max_chase_bps=12.0,
        idea_ttl_minutes=15,
        stop_loss=69500.0,
        take_profit=70800.0,
        risk_pct_cap=0.01,
        leverage_cap=1.0,
        expires_at=utcnow_naive() - timedelta(hours=2),
        triggered_at=utcnow_naive() - timedelta(hours=3),
        idempotency_key="stale-triggered-cleanup-test",
        metadata_json={"execution_result": {"status": "partially_filled"}},
    )
    db_session.add(stale_plan)
    db_session.flush()

    result = run_due_entry_plan_watcher_cycle(db_session)

    assert result is not None
    assert result["stale_plan_cleanup"]["normalized_count"] == 1
    assert stale_plan.plan_status == "expired"
    assert stale_plan.canceled_reason == "PLAN_TRIGGERED_STALE_HISTORY_CLEANED_BY_WATCHER"


def test_transition_watch_direct_entry_is_bounded_to_pending_plan(monkeypatch, db_session) -> None:
    class DirectEntryProvider(_CountingDecisionProvider):
        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            result = super().generate(role, payload, response_model=response_model, instructions=instructions)
            result.output.update(
                {
                    "decision": "long",
                    "entry_zone_min": 69800.0,
                    "entry_zone_max": 69950.0,
                    "entry_mode": "pullback_confirm",
                    "invalidation_price": 69400.0,
                    "max_chase_bps": 12.0,
                    "idea_ttl_minutes": 18,
                    "stop_loss": 69400.0,
                    "take_profit": 71000.0,
                    "rationale_codes": ["TRANSITION_WATCH_DIRECT_LONG"],
                    "no_trade_reason_codes": [],
                }
            )
            return result

    def fail_execute(*args, **kwargs):
        raise AssertionError("transition-watch AI must not submit a direct order")

    monkeypatch.setattr("trading_mvp.services.orchestrator.evaluate_risk", _entry_plan_fake_evaluate_risk)
    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    provider = DirectEntryProvider(decision="long")
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
        selection_context={
            "assigned_slot": None,
            "candidate_weight": 0.0,
            "selected": False,
            "selection_reason": "score_below_threshold",
            "rejected_reason": "score_below_threshold",
            "entry_score_threshold": 0.40,
            "score": {"total_score": 0.38},
            "ai_call_policy": {"soft_signal_review_mode": "transition_watch"},
        },
    )
    pending_plan = db_session.scalar(select(PendingEntryPlan).limit(1))

    assert provider.calls == 1
    assert result["decision"]["decision"] == "hold"
    assert result["status"] == "entry_plan_armed"
    assert result["entry_plan"]["side"] == "long"
    assert pending_plan is not None
    assert pending_plan.plan_status == "armed"
    assert "SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_WATCH" in pending_plan.rationale_codes
    assert decision_row.metadata_json["transition_watch_ai_invoked"] is True
    assert decision_row.metadata_json["watch_only_bounded"] is True
    assert decision_row.metadata_json["soft_signal_bounded_action"] == "watch_entry_plan"
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_transition_watch_invalid_direct_entry_is_bounded_to_hold(monkeypatch, db_session) -> None:
    def fail_execute(*args, **kwargs):
        raise AssertionError("invalid transition-watch direct entry must not submit an order")

    monkeypatch.setattr("trading_mvp.services.orchestrator.evaluate_risk", _entry_plan_fake_evaluate_risk)
    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    provider = _CountingDecisionProvider(decision="long")
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
        selection_context={
            "assigned_slot": None,
            "candidate_weight": 0.0,
            "selected": False,
            "selection_reason": "score_below_threshold",
            "rejected_reason": "score_below_threshold",
            "entry_score_threshold": 0.40,
            "score": {"total_score": 0.38},
            "ai_call_policy": {"soft_signal_review_mode": "transition_watch"},
        },
    )

    assert provider.calls == 1
    assert result["decision"]["decision"] == "hold"
    assert result["entry_plan"] is None
    assert "SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_HOLD" in result["decision"]["no_trade_reason_codes"]
    assert decision_row.metadata_json["transition_watch_ai_invoked"] is True
    assert decision_row.metadata_json["watch_only_bounded"] is True
    assert decision_row.metadata_json["soft_signal_bounded_action"] == "hold"
    assert db_session.scalar(select(PendingEntryPlan).limit(1)) is None
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_hold_watch_entry_plan_waits_when_baseline_disagrees(monkeypatch, db_session) -> None:
    class WatchPlanProvider(_CountingDecisionProvider):
        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            result = super().generate(role, payload, response_model=response_model, instructions=instructions)
            result.output["watch_entry_plan"] = {
                "side": "long",
                "entry_zone_min": 69800.0,
                "entry_zone_max": 69950.0,
                "entry_mode": "pullback_confirm",
                "invalidation_price": 69400.0,
                "max_chase_bps": 12.0,
                "idea_ttl_minutes": 18,
                "stop_loss": 69400.0,
                "take_profit": 71000.0,
                "reason_codes": ["WATCH_SUPPORT_RECLAIM"],
            }
            return result

    def fail_execute(*args, **kwargs):
        raise AssertionError("baseline-disagreed watch plan must not execute immediately")

    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.evaluate_risk",
        _entry_plan_fake_evaluate_risk_baseline_disagreement,
    )
    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    provider = WatchPlanProvider(decision="hold")
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
    )
    pending_plan = db_session.scalar(select(PendingEntryPlan).limit(1))
    watch_risk = db_session.scalar(
        select(RiskCheck)
        .where(RiskCheck.decision_run_id == decision_row.id, RiskCheck.decision == "long")
        .limit(1)
    )

    assert result["decision"]["decision"] == "hold"
    assert result["status"] == "entry_plan_armed"
    assert result["entry_plan"]["side"] == "long"
    assert pending_plan is not None
    assert pending_plan.plan_status == "armed"
    assert pending_plan.source_decision_run_id == decision_row.id
    assert pending_plan.metadata_json["source_blocked_reason_codes"] == ["DETERMINISTIC_BASELINE_DISAGREEMENT"]
    assert watch_risk is not None
    assert watch_risk.allowed is False
    assert watch_risk.reason_codes == ["DETERMINISTIC_BASELINE_DISAGREEMENT"]
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_hold_watch_entry_plan_does_not_arm_when_protection_unverified(monkeypatch, db_session) -> None:
    class WatchPlanProvider(_CountingDecisionProvider):
        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            result = super().generate(role, payload, response_model=response_model, instructions=instructions)
            result.output["watch_entry_plan"] = {
                "side": "long",
                "entry_zone_min": 69800.0,
                "entry_zone_max": 69950.0,
                "entry_mode": "pullback_confirm",
                "invalidation_price": 69400.0,
                "max_chase_bps": 12.0,
                "idea_ttl_minutes": 18,
                "stop_loss": 69400.0,
                "take_profit": 71000.0,
                "reason_codes": ["WATCH_SUPPORT_RECLAIM"],
            }
            return result

    def fail_execute(*args, **kwargs):
        raise AssertionError("protection-unverified watch plan must not execute")

    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.evaluate_risk",
        _entry_plan_fake_evaluate_risk_protection_unverified,
    )
    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    provider = WatchPlanProvider(decision="hold")
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
    )
    watch_risk = db_session.scalar(
        select(RiskCheck)
        .where(RiskCheck.decision_run_id == decision_row.id, RiskCheck.decision == "long")
        .limit(1)
    )

    assert result["decision"]["decision"] == "hold"
    assert result["status"] == "completed"
    assert result["entry_plan"] is None
    assert db_session.scalar(select(PendingEntryPlan).limit(1)) is None
    assert watch_risk is not None
    assert watch_risk.allowed is False
    assert watch_risk.reason_codes == ["DETERMINISTIC_BASELINE_DISAGREEMENT", "PROTECTION_STATE_UNVERIFIED"]
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_entry_candidate_pause_hard_skips_ai(monkeypatch, db_session) -> None:
    provider = _CountingDecisionProvider(decision="long")
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
        settings_mutator=lambda settings_row: setattr(settings_row, "trading_paused", True),
        auto_resume_checked=True,
    )
    audit = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "decision_ai_skipped", AuditEvent.entity_id == str(decision_row.id))
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )

    assert provider.calls == 0
    assert result["last_ai_skip_reason"] == "pause"
    assert decision_row.metadata_json["ai_call_policy"]["hard_skip_ai"] is True
    assert decision_row.metadata_json["ai_call_policy"]["reason"] == "pause"
    assert audit is not None
    assert audit.payload["ai_call_event"] == "AI_CALL_SKIPPED"
    assert audit.payload["reason"] == "pause"
    assert audit.payload["hard_skip_ai"] is True
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_entry_candidate_stale_market_data_hard_skips_ai(monkeypatch, db_session) -> None:
    provider = _CountingDecisionProvider(decision="long")
    stale_snapshot = _pre_ai_gate_snapshot().model_copy(update={"is_stale": True})
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
        snapshot=stale_snapshot,
    )
    audit = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "decision_ai_skipped", AuditEvent.entity_id == str(decision_row.id))
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )

    assert provider.calls == 0
    assert result["last_ai_skip_reason"] == "stale_market_data"
    assert decision_row.metadata_json["ai_call_policy"]["hard_skip_ai"] is True
    assert audit is not None
    assert audit.payload["reason"] == "stale_market_data"
    assert audit.payload["hard_skip_ai"] is True
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_entry_candidate_account_untrusted_hard_skips_ai(monkeypatch, db_session) -> None:
    provider = _CountingDecisionProvider(decision="long")
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
        sync_fresh=False,
    )
    audit = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "decision_ai_skipped", AuditEvent.entity_id == str(decision_row.id))
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )

    assert provider.calls == 0
    assert result["last_ai_skip_reason"] == "account_untrusted"
    assert decision_row.metadata_json["ai_call_policy"]["hard_skip_ai"] is True
    assert audit is not None
    assert audit.payload["reason"] == "account_untrusted"
    assert audit.payload["hard_skip_ai"] is True
    assert "account_untrusted" in audit.payload["hard_skip_reason_codes"]
    assert any(
        reason_code.endswith("_sync_stale") or reason_code.endswith("_sync_incomplete")
        for reason_code in audit.payload["hard_skip_reason_codes"]
    )
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_entry_candidate_daily_loss_limit_hard_skips_ai(monkeypatch, db_session) -> None:
    db_session.add(
        PnLSnapshot(
            snapshot_date=utcnow_naive().date(),
            equity=100_000.0,
            cash_balance=100_000.0,
            wallet_balance=100_000.0,
            available_balance=100_000.0,
            gross_realized_pnl=-6_000.0,
            fee_total=0.0,
            funding_total=0.0,
            net_pnl=-6_000.0,
            realized_pnl=-6_000.0,
            unrealized_pnl=0.0,
            daily_pnl=-6_000.0,
            cumulative_pnl=-6_000.0,
            consecutive_losses=1,
        )
    )
    db_session.flush()
    provider = _CountingDecisionProvider(decision="long")

    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
    )

    assert provider.calls == 0
    assert result["last_ai_skip_reason"] == "DAILY_LOSS_LIMIT_REACHED"
    assert decision_row.metadata_json["ai_call_policy"]["hard_skip_ai"] is True
    assert "DAILY_LOSS_LIMIT_REACHED" in decision_row.metadata_json["ai_call_policy"]["hard_skip_reason_codes"]
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_entry_candidate_live_approval_required_hard_skips_ai(monkeypatch, db_session) -> None:
    defaults = get_app_settings().model_copy(update={"live_trading_env_enabled": True})
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.get_settings",
        lambda: defaults,
    )
    provider = _CountingDecisionProvider(decision="long")

    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
        settings_mutator=lambda settings_row: (
            setattr(settings_row, "live_trading_enabled", True),
            setattr(settings_row, "rollout_mode", "limited_live"),
            setattr(settings_row, "manual_live_approval", True),
            setattr(settings_row, "live_execution_armed", False),
        ),
    )

    assert provider.calls == 0
    assert result["last_ai_skip_reason"] == "LIVE_APPROVAL_REQUIRED"
    assert decision_row.metadata_json["ai_call_policy"]["hard_skip_ai"] is True
    assert "LIVE_APPROVAL_REQUIRED" in decision_row.metadata_json["ai_call_policy"]["hard_skip_reason_codes"]
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_entry_candidate_ai_invoked_hold_remains_ai_called_and_risk_hold(monkeypatch, db_session) -> None:
    provider = _CountingDecisionProvider(decision="hold")
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
    )
    invoked_audit = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "decision_ai_invoked", AuditEvent.entity_id == str(decision_row.id))
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    skipped_audit = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "decision_ai_skipped", AuditEvent.entity_id == str(decision_row.id))
        .limit(1)
    )

    assert provider.calls == 1
    assert result["decision"]["decision"] == "hold"
    assert result["risk_check_id"] is not None
    assert result["risk_result"]["allowed"] is False
    assert "HOLD_DECISION" in result["risk_result"]["reason_codes"]
    assert result["execution"] is None
    assert result["last_ai_trigger_reason"] == "entry_candidate_event"
    assert result["last_ai_invoked_at"] is not None
    assert result["last_ai_skip_reason"] is None
    assert result["ai_skipped_reason"] is None
    assert decision_row.provider_name == "openai"
    assert decision_row.metadata_json["source"] == "llm"
    assert decision_row.metadata_json["last_ai_skip_reason"] is None
    assert decision_row.metadata_json["pre_ai_skip_reason"] is None
    assert invoked_audit is not None
    assert invoked_audit.payload["trigger"]["trigger_reason"] == "entry_candidate_event"
    assert skipped_audit is None
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None


def test_entry_candidate_high_impact_event_records_conservative_ai_context(monkeypatch, db_session) -> None:
    provider = _CountingDecisionProvider(decision="hold")
    event_context = _pre_ai_gate_macro_event_context(minutes_to_event=10)
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(
            volume_ratio=1.08,
            weak_volume=False,
            event_context=event_context,
        ),
        provider=provider,
    )
    input_ai_context = decision_row.input_payload["ai_context"]

    assert provider.calls == 1
    assert result["last_ai_skip_reason"] is None
    assert input_ai_context["event_risk_active"] is True
    assert "MACRO_EVENT_IMMINENT" in input_ai_context["event_risk_reason_codes"]
    assert decision_row.metadata_json["event_risk_active"] is True
    assert "MACRO_EVENT_RISK_WINDOW_ACTIVE" in decision_row.metadata_json["event_risk_reason_codes"]
    assert decision_row.metadata_json["event_risk_context"]["risk_pct_multiplier"] == 0.5
    assert decision_row.metadata_json["event_risk_context"]["event_bias_used"] == "bearish"


def test_decision_ai_context_includes_position_plan_and_execution_summaries(monkeypatch, db_session) -> None:
    provider = _CountingDecisionProvider(decision="hold")
    now = utcnow_naive()
    db_session.add(
        PendingEntryPlan(
            symbol="BTCUSDT",
            side="long",
            plan_status="armed",
            source_decision_run_id=None,
            regime="bullish",
            posture="bullish_pullback",
            rationale_codes=["AI_WATCH_ENTRY_PLAN"],
            source_timeframe="15m",
            entry_mode="pullback_confirm",
            entry_zone_min=69900.0,
            entry_zone_max=70050.0,
            invalidation_price=69400.0,
            max_chase_bps=18.0,
            idea_ttl_minutes=45,
            stop_loss=69400.0,
            take_profit=71200.0,
            risk_pct_cap=0.01,
            leverage_cap=2.0,
            expires_at=now + timedelta(minutes=45),
            idempotency_key="test-ai-context-pending-plan",
            metadata_json={"holding_profile": "scalp"},
        )
    )
    open_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=70120.0,
        leverage=2.0,
        stop_loss=69400.0,
        take_profit=71200.0,
        realized_pnl=0.0,
        unrealized_pnl=12.0,
        metadata_json={},
    )

    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=1.08, weak_volume=False),
        provider=provider,
        open_position=open_position,
    )
    input_ai_context = decision_row.input_payload["ai_context"]

    assert provider.calls == 1
    assert result["decision"]["decision"] == "hold"
    assert input_ai_context["active_position_summary"]["has_open_position"] is True
    assert input_ai_context["active_position_summary"]["symbol"] == "BTCUSDT"
    assert input_ai_context["active_position_summary"]["current_r_multiple"] == 0.2
    assert input_ai_context["pending_entry_plan_summary"]["active_plan_count"] == 1
    assert input_ai_context["pending_entry_plan_summary"]["same_symbol_plan_count"] == 1
    assert input_ai_context["pending_entry_plan_summary"]["plans"][0]["entry_zone_min"] == 69900.0
    assert input_ai_context["execution_constraints_summary"]["risk_guard_final_authority"] is True
    assert input_ai_context["execution_constraints_summary"]["ai_output_executes_directly"] is False


def test_entry_candidate_weak_but_not_extreme_volume_keeps_ai_candidate_event(monkeypatch, db_session) -> None:
    provider = _CountingDecisionProvider()
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="entry_candidate_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=0.45, weak_volume=True, volume_regime="weak"),
        provider=provider,
    )

    assert provider.calls == 1
    assert result["last_ai_trigger_reason"] == "entry_candidate_event"
    assert result["last_ai_skip_reason"] is None
    assert decision_row.metadata_json["pre_ai_skip_reason"] is None


def test_cadence_flags_keep_quiet_range_weak_volume_watchable() -> None:
    feature_payload = _pre_ai_gate_feature(volume_ratio=0.55, weak_volume=True, volume_regime="weak")
    feature_payload.regime.primary_regime = "range"
    feature_payload.regime.trend_alignment = "range"
    feature_payload.regime.momentum_state = "weakening"
    feature_payload.regime.momentum_weakening = True
    feature_payload.breakout.range_width_pct = 0.28
    feature_payload.breakout.range_breakout_direction = "none"
    feature_payload.location.range_position_pct = 0.30
    feature_payload.volume_persistence.persistence_ratio = 0.62
    feature_payload.derivatives.spread_bps = 4.0

    flags = TradingOrchestrator._cadence_feature_flags(feature_payload)

    assert flags["quiet_range_mean_reversion_allowed"] is True
    assert flags["no_trade_zone"] is False


def test_protection_review_weak_volume_does_not_use_entry_preai_gate(monkeypatch, db_session) -> None:
    provider = _CountingDecisionProvider()
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="protection_review_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=0.07, weak_volume=True, volume_regime="weak"),
        provider=provider,
    )

    assert provider.calls == 0
    assert result["last_ai_skip_reason"] == "PROTECTION_REVIEW_DETERMINISTIC_ONLY"
    assert decision_row.metadata_json["pre_ai_skip_reason"] is None
    assert decision_row.metadata_json["last_ai_skip_reason"] != "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"


def test_manual_review_weak_volume_does_not_use_entry_preai_gate(monkeypatch, db_session) -> None:
    provider = _CountingDecisionProvider()
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="manual_review_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=0.07, weak_volume=True, volume_regime="weak"),
        provider=provider,
    )

    assert provider.calls == 1
    assert result["last_ai_trigger_reason"] == "manual_review_event"
    assert result["last_ai_skip_reason"] is None
    assert decision_row.metadata_json["pre_ai_skip_reason"] is None


def test_manual_open_position_reduce_route_weak_volume_does_not_use_entry_preai_gate(
    monkeypatch,
    db_session,
) -> None:
    provider = _CountingDecisionProvider(decision="reduce")
    result, decision_row = _run_pre_ai_gate_decision(
        monkeypatch,
        db_session,
        trigger_reason="manual_review_event",
        feature_payload=_pre_ai_gate_feature(volume_ratio=0.07, weak_volume=True, volume_regime="weak"),
        provider=provider,
        open_position=_pre_ai_gate_open_position(),
        selection_context={
            "assigned_slot": "slot_1",
            "candidate_weight": 0.64,
            "strategy_engine": "trend_pullback_engine",
            "strategy_engine_context": {"management_only_open_position_route": True},
            "holding_profile": "scalp",
            "holding_profile_reason": "open_position_management",
        },
    )

    assert provider.calls == 1
    assert result["decision"]["decision"] == "reduce"
    assert result["last_ai_skip_reason"] is None
    assert decision_row.metadata_json["pre_ai_skip_reason"] is None
    assert "reduce" in decision_row.metadata_json["allowed_actions"]
    assert "exit" in decision_row.metadata_json["allowed_actions"]


def test_repeated_same_event_dedupes_ai(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    now = utcnow_naive()
    db_session.add(
        AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="openai",
            summary="prior trigger",
            input_payload={
                "market_snapshot": {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "snapshot_time": now.isoformat(),
                }
            },
            output_payload={"symbol": "BTCUSDT", "timeframe": "15m", "decision": "long"},
            metadata_json={
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "source": "llm",
                "ai_trigger": {
                    "trigger_reason": "entry_candidate_event",
                    "trigger_fingerprint": "same-fingerprint",
                },
            },
            schema_valid=True,
            started_at=now - timedelta(minutes=5),
            completed_at=now - timedelta(minutes=5),
        )
    )
    db_session.flush()

    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "selected_symbols": ["BTCUSDT"],
            "skipped_symbols": [],
            "rankings": [_ranking_candidate_payload(symbol="BTCUSDT")],
        },
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_trigger_fingerprint",
        staticmethod(lambda payload: "same-fingerprint"),
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_decision_cycle",
        lambda self, **kwargs: pytest.fail("deduped trigger must not invoke decision cycle"),
    )

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")

    assert result["results"][0]["outcome"]["ai_review_status"] == "deduped"
    assert result["results"][0]["outcome"]["trigger_deduped"] is True
    assert result["results"][0]["outcome"]["trigger"]["trigger_fingerprint"] == "same-fingerprint"


def test_direct_decision_path_keeps_slot_context(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    snapshot_time = utcnow_naive()
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=3,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=2), open=69950.0, high=70040.0, low=69920.0, close=70000.0, volume=900.0),
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=1), open=70000.0, high=70070.0, low=69980.0, close=70030.0, volume=980.0),
            MarketCandle(timestamp=snapshot_time, open=70030.0, high=70090.0, low=70010.0, close=70060.0, volume=1020.0),
        ],
        derivatives_context=DerivativesContextPayload(
            source="binance_public",
            spread_bps=2.0,
            spread_stress_score=0.2,
        ),
    )
    feature_payload = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.32,
        volatility_pct=0.002,
        volume_ratio=1.08,
        drawdown_pct=0.001,
        rsi=58.0,
        atr=90.0,
        atr_pct=0.0013,
        momentum_score=0.18,
        multi_timeframe={
            "15m": TimeframeFeatureContext(
                timeframe="15m",
                trend_score=0.32,
                volatility_pct=0.002,
                volume_ratio=1.08,
                drawdown_pct=0.001,
                rsi=58.0,
                atr=90.0,
                atr_pct=0.0013,
                momentum_score=0.18,
            )
        },
        regime=RegimeFeatureContext(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            volume_regime="normal",
            momentum_state="strengthening",
            weak_volume=False,
            momentum_weakening=False,
        ),
        data_quality_flags=[],
    )

    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": ["BTCUSDT"],
            "skipped_symbols": [],
            "rankings": [_ranking_candidate_payload(symbol="BTCUSDT")],
        },
    )
    monkeypatch.setattr("trading_mvp.services.orchestrator.compute_features", lambda *args, **kwargs: feature_payload)

    class DummyGate:
        allowed = True
        reason = "allowed"

        def as_metadata(self) -> dict[str, object]:
            return {"allowed": True, "reason": "allowed"}

    monkeypatch.setattr("trading_mvp.services.orchestrator.get_openai_call_gate", lambda *args, **kwargs: DummyGate())
    def fake_build_ai_prior_context(*args, **kwargs):  # noqa: ANN002, ANN003
        debug_collector = kwargs.get("debug_collector")
        if isinstance(debug_collector, dict):
            debug_collector.update(
                {
                    "prior_read_path": "cache_hit",
                    "cache_applied": True,
                    "cache_fallback_used": False,
                }
            )
        return AIPriorContextPacket(
            engine_prior_available=True,
            engine_prior_sample_count=3,
            engine_sample_threshold_satisfied=True,
            engine_prior_classification="strong",
            capital_efficiency_available=True,
            capital_efficiency_sample_count=3,
            capital_efficiency_sample_threshold_satisfied=True,
            capital_efficiency_classification="efficient",
            session_prior_available=True,
            session_prior_sample_count=6,
            session_sample_threshold_satisfied=True,
            session_prior_classification="neutral",
            session_prior_recency_minutes=135.0,
            time_of_day_prior_available=True,
            time_of_day_prior_sample_count=7,
            time_of_day_sample_threshold_satisfied=True,
            time_of_day_prior_classification="weak",
            time_of_day_prior_recency_minutes=165.0,
            session_time_calibration_reason_codes=["TIME_OF_DAY_PRIOR_STRONG_SAMPLE_EDGE"],
            prior_reason_codes=["ENGINE_PRIOR_STRONG"],
            prior_penalty_level="none",
            expected_payoff_efficiency_hint_summary={"time_to_0_25r_hint_minutes": 18.0},
        )

    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_ai_prior_context",
        fake_build_ai_prior_context,
    )

    captured_selection_context: dict[str, object] = {}
    captured_ai_context: dict[str, object] = {}

    def fake_run(_market_snapshot, _feature_payload, _open_positions, risk_context, *, use_ai, **kwargs):
        captured_selection_context.update(dict(risk_context.get("selection_context") or {}))
        ai_context = kwargs.get("ai_context")
        if ai_context is not None and hasattr(ai_context, "model_dump"):
            captured_ai_context.update(ai_context.model_dump(mode="json"))
        return (
            TradeDecision(
                decision="hold",
                confidence=0.41,
                symbol="BTCUSDT",
                timeframe="15m",
                entry_zone_min=None,
                entry_zone_max=None,
                entry_mode="none",
                invalidation_price=None,
                max_chase_bps=None,
                idea_ttl_minutes=None,
                stop_loss=None,
                take_profit=None,
                max_holding_minutes=60,
                risk_pct=0.01,
                leverage=1.0,
                rationale_codes=["DIRECT_PATH_TEST"],
                explanation_short="manual review hold",
                explanation_detailed="Manual review path keeps selection context for audit and risk plumbing.",
            ),
            "deterministic-mock",
            {
                "source": "deterministic",
                "session_prior_sample_count": 6,
                "time_of_day_prior_sample_count": 7,
                "session_prior_recency_minutes": 135.0,
                "time_of_day_prior_recency_minutes": 165.0,
                "session_time_calibration_reason_codes": ["TIME_OF_DAY_PRIOR_STRONG_SAMPLE_EDGE"],
                "session_time_penalty_applied": False,
            },
        )

    orchestrator = TradingOrchestrator(db_session)
    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_run)

    result = orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="manual",
        exchange_sync_checked=True,
        market_snapshot_override=snapshot,
        market_context_override={
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
        },
        review_trigger={
            "trigger_reason": "manual_review_event",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "strategy_engine": "trend_pullback_engine",
            "holding_profile": "scalp",
            "assigned_slot": "slot_1",
            "candidate_weight": 0.64,
            "reason_codes": ["TEST_SELECTION"],
            "trigger_fingerprint": "manual-trigger-1234",
            "fingerprint_basis": {"position_state_bucket": "flat"},
            "fingerprint_changed_fields": ["position_state_bucket"],
            "last_decision_at": snapshot_time.isoformat(),
            "last_material_review_at": snapshot_time.isoformat(),
            "forced_review_reason": "OPEN_POSITION_MAX_REVIEW_AGE_EXCEEDED",
            "triggered_at": snapshot_time.isoformat(),
        },
    )
    decision_row = db_session.get(AgentRun, result["decision_run_id"])

    assert captured_selection_context["assigned_slot"] == "slot_1"
    assert captured_selection_context["candidate_weight"] == 0.64
    assert captured_selection_context["slot_allocation"]["assigned_slot"] == "slot_1"
    assert captured_ai_context["assigned_slot"] == "slot_1"
    assert captured_ai_context["candidate_weight"] == 0.64
    assert captured_ai_context["strategy_engine"] == "trend_pullback_engine"
    assert captured_ai_context["holding_profile"] == "scalp"
    assert captured_ai_context["hard_stop_active"] is True
    assert captured_ai_context["prior_context"]["engine_prior_classification"] == "strong"
    assert captured_ai_context["prior_context"]["session_prior_recency_minutes"] == 135.0
    assert captured_ai_context["prior_context"]["time_of_day_prior_recency_minutes"] == 165.0
    assert captured_ai_context["prior_context"]["session_time_calibration_reason_codes"] == [
        "TIME_OF_DAY_PRIOR_STRONG_SAMPLE_EDGE"
    ]
    assert decision_row is not None
    assert decision_row.input_payload["ai_context"]["assigned_slot"] == "slot_1"
    assert decision_row.input_payload["ai_context"]["strategy_engine"] == "trend_pullback_engine"
    assert decision_row.input_payload["ai_context"]["prior_context"]["engine_prior_classification"] == "strong"
    assert decision_row.input_payload["ai_context"]["prior_context"]["session_prior_sample_count"] == 6
    assert decision_row.input_payload["ai_context"]["prior_context"]["time_of_day_prior_sample_count"] == 7
    assert decision_row.input_payload["ai_trigger"]["fingerprint_changed_fields"] == ["position_state_bucket"]
    assert decision_row.metadata_json["slot_allocation"]["assigned_slot"] == "slot_1"
    assert decision_row.metadata_json["ai_context"]["holding_profile"] == "scalp"
    assert decision_row.metadata_json["session_prior_recency_minutes"] == 135.0
    assert decision_row.metadata_json["time_of_day_prior_recency_minutes"] == 165.0
    assert decision_row.metadata_json["session_time_calibration_reason_codes"] == [
        "TIME_OF_DAY_PRIOR_STRONG_SAMPLE_EDGE"
    ]
    assert decision_row.metadata_json["session_time_penalty_applied"] is False
    assert decision_row.metadata_json["fingerprint_changed_fields"] == ["position_state_bucket"]
    assert decision_row.metadata_json["forced_review_reason"] == "OPEN_POSITION_MAX_REVIEW_AGE_EXCEEDED"
    assert decision_row.metadata_json["last_material_review_at"] is not None
    assert decision_row.metadata_json["prior_read_path"] == "cache_hit"
    assert decision_row.metadata_json["cache_applied"] is True
    assert decision_row.metadata_json["cache_fallback_used"] is False
    assert result["last_ai_trigger_reason"] == "manual_review_event"
    assert result["fingerprint_changed_fields"] == ["position_state_bucket"]
    assert result["forced_review_reason"] == "OPEN_POSITION_MAX_REVIEW_AGE_EXCEEDED"


def test_manual_open_position_review_surfaces_cadence_metadata(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    snapshot_time = utcnow_naive()
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=3,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=2), open=69950.0, high=70040.0, low=69920.0, close=70000.0, volume=900.0),
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=1), open=70000.0, high=70070.0, low=69980.0, close=70030.0, volume=980.0),
            MarketCandle(timestamp=snapshot_time, open=70030.0, high=70090.0, low=70010.0, close=70060.0, volume=1020.0),
        ],
        derivatives_context=DerivativesContextPayload(
            source="binance_public",
            spread_bps=2.0,
            spread_stress_score=0.2,
        ),
    )
    feature_payload = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.32,
        volatility_pct=0.002,
        volume_ratio=1.08,
        drawdown_pct=0.001,
        rsi=58.0,
        atr=90.0,
        atr_pct=0.0013,
        momentum_score=0.18,
        multi_timeframe={
            "15m": TimeframeFeatureContext(
                timeframe="15m",
                trend_score=0.32,
                volatility_pct=0.002,
                volume_ratio=1.08,
                drawdown_pct=0.001,
                rsi=58.0,
                atr=90.0,
                atr_pct=0.0013,
                momentum_score=0.18,
            )
        },
        regime=RegimeFeatureContext(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            volume_regime="normal",
            momentum_state="strengthening",
            weak_volume=False,
            momentum_weakening=False,
        ),
        data_quality_flags=[],
    )
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=70120.0,
            leverage=2.0,
            stop_loss=69400.0,
            take_profit=71200.0,
            metadata_json={
                "position_management": {
                    "holding_profile": "position",
                    "holding_profile_reason": "manual_position_review",
                    "hard_stop_active": True,
                    "stop_widening_allowed": False,
                }
            },
        )
    )
    db_session.flush()

    monkeypatch.setattr("trading_mvp.services.orchestrator.compute_features", lambda *args, **kwargs: feature_payload)

    class DummyGate:
        allowed = True
        reason = "allowed"

        def as_metadata(self) -> dict[str, object]:
            return {"allowed": True, "reason": "allowed"}

    monkeypatch.setattr("trading_mvp.services.orchestrator.get_openai_call_gate", lambda *args, **kwargs: DummyGate())

    def fake_build_ai_prior_context(*args, **kwargs):  # noqa: ANN002, ANN003
        return AIPriorContextPacket(
            engine_prior_available=True,
            engine_prior_sample_count=3,
            engine_sample_threshold_satisfied=True,
            engine_prior_classification="strong",
            capital_efficiency_available=True,
            capital_efficiency_sample_count=3,
            capital_efficiency_sample_threshold_satisfied=True,
            capital_efficiency_classification="efficient",
            prior_reason_codes=["ENGINE_PRIOR_STRONG"],
            prior_penalty_level="none",
            expected_payoff_efficiency_hint_summary={"time_to_0_25r_hint_minutes": 18.0},
        )

    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_ai_prior_context",
        fake_build_ai_prior_context,
    )

    def fake_run(_market_snapshot, _feature_payload, _open_positions, _risk_context, *, use_ai, **kwargs):
        return (
            TradeDecision(
                decision="hold",
                confidence=0.44,
                symbol="BTCUSDT",
                timeframe="15m",
                entry_zone_min=None,
                entry_zone_max=None,
                entry_mode="none",
                invalidation_price=None,
                max_chase_bps=None,
                idea_ttl_minutes=None,
                stop_loss=None,
                take_profit=None,
                max_holding_minutes=180,
                risk_pct=0.01,
                leverage=1.0,
                rationale_codes=["MANUAL_POSITION_REVIEW"],
                explanation_short="manual open-position review hold",
                explanation_detailed="Manual open-position review keeps cadence metadata visible.",
            ),
            "deterministic-mock",
            {"source": "deterministic"},
        )

    orchestrator = TradingOrchestrator(db_session)
    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_run)

    result = orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="manual",
        exchange_sync_checked=True,
        market_snapshot_override=snapshot,
        market_context_override={
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
        },
        review_trigger={
            "trigger_reason": "manual_review_event",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "strategy_engine": "trend_pullback_engine",
            "holding_profile": "position",
            "reason_codes": ["MANUAL_REVIEW"],
            "trigger_fingerprint": "manual-position-trigger-1234",
            "fingerprint_basis": {"position_state_bucket": "profit"},
            "fingerprint_changed_fields": ["position_state_bucket"],
            "last_decision_at": snapshot_time.isoformat(),
            "last_material_review_at": snapshot_time.isoformat(),
            "triggered_at": snapshot_time.isoformat(),
        },
    )
    decision_row = db_session.get(AgentRun, result["decision_run_id"])

    assert decision_row is not None
    assert decision_row.input_payload["ai_trigger"]["applied_review_cadence_minutes"] == 30
    assert decision_row.input_payload["ai_trigger"]["review_cadence_source"] == "holding_profile_cadence_hint"
    assert decision_row.input_payload["ai_trigger"]["holding_profile_cadence_hint"]["decision_interval_minutes"] == 30
    assert decision_row.metadata_json["applied_review_cadence_minutes"] == 30
    assert decision_row.metadata_json["review_cadence_source"] == "holding_profile_cadence_hint"
    assert decision_row.metadata_json["max_review_age_minutes"] == 90
    assert result["applied_review_cadence_minutes"] == 30
    assert result["review_cadence_source"] == "holding_profile_cadence_hint"
    assert result["holding_profile_cadence_hint"]["holding_profile"] == "position"
    assert result["max_review_age_minutes"] == 90


def test_pipeline_persists_management_intent_semantics_for_protection_restore(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    snapshot_time = utcnow_naive()
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=3,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=2), open=69950.0, high=70040.0, low=69920.0, close=70000.0, volume=900.0),
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=1), open=70000.0, high=70070.0, low=69980.0, close=70030.0, volume=980.0),
            MarketCandle(timestamp=snapshot_time, open=70030.0, high=70090.0, low=70010.0, close=70060.0, volume=1020.0),
        ],
    )
    feature_payload = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.32,
        volatility_pct=0.002,
        volume_ratio=1.08,
        drawdown_pct=0.001,
        rsi=58.0,
        atr=90.0,
        atr_pct=0.0013,
        momentum_score=0.18,
        multi_timeframe={
            "15m": TimeframeFeatureContext(
                timeframe="15m",
                trend_score=0.32,
                volatility_pct=0.002,
                volume_ratio=1.08,
                drawdown_pct=0.001,
                rsi=58.0,
                atr=90.0,
                atr_pct=0.0013,
                momentum_score=0.18,
            )
        },
        regime=RegimeFeatureContext(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            volume_regime="normal",
            momentum_state="strengthening",
            weak_volume=False,
            momentum_weakening=False,
        ),
        data_quality_flags=[],
    )
    open_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=69900.0,
        mark_price=70060.0,
        leverage=2.0,
        stop_loss=69780.0,
        take_profit=70320.0,
        realized_pnl=0.0,
        unrealized_pnl=1.6,
    )
    db_session.add(open_position)
    db_session.flush()

    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": ["BTCUSDT"],
            "skipped_symbols": [],
            "rankings": [_ranking_candidate_payload(symbol="BTCUSDT", strategy_engine="protection_reduce_engine")],
        },
    )
    monkeypatch.setattr("trading_mvp.services.orchestrator.compute_features", lambda *args, **kwargs: feature_payload)

    class DummyGate:
        allowed = True
        reason = "allowed"

        def as_metadata(self) -> dict[str, object]:
            return {"allowed": True, "reason": "allowed"}

    monkeypatch.setattr("trading_mvp.services.orchestrator.get_openai_call_gate", lambda *args, **kwargs: DummyGate())

    def fake_run(_market_snapshot, _feature_payload, _open_positions, _risk_context, *, use_ai, **kwargs):
        return (
            TradeDecision(
                decision="long",
                confidence=0.63,
                symbol="BTCUSDT",
                timeframe="15m",
                entry_zone_min=69980.0,
                entry_zone_max=70020.0,
                entry_mode="immediate",
                invalidation_price=69820.0,
                max_chase_bps=10.0,
                idea_ttl_minutes=20,
                stop_loss=69820.0,
                take_profit=70300.0,
                max_holding_minutes=120,
                risk_pct=0.01,
                leverage=1.0,
                rationale_codes=["PROTECTION_REQUIRED", "PROTECTION_RESTORE"],
                explanation_short="protection restore compatibility",
                explanation_detailed="Legacy long semantics are preserved for execution compatibility but external metadata must classify this as protection restore.",
            ),
            "deterministic-mock",
            {"source": "deterministic"},
        )

    orchestrator = TradingOrchestrator(db_session)
    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_run)

    result = orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="manual",
        exchange_sync_checked=True,
        market_snapshot_override=snapshot,
        market_context_override={
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
        },
    )
    decision_row = db_session.get(AgentRun, result["decision_run_id"])

    assert decision_row is not None
    assert decision_row.output_payload["decision"] == "long"
    assert decision_row.output_payload["intent_family"] == "protection"
    assert decision_row.output_payload["management_action"] == "restore_protection"
    assert decision_row.output_payload["legacy_semantics_preserved"] is True
    assert decision_row.output_payload["analytics_excluded_from_entry_stats"] is True
    assert decision_row.metadata_json["intent_family"] == "protection"
    assert decision_row.metadata_json["management_action"] == "restore_protection"


def test_pipeline_persists_bounded_breakout_output_metadata(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    snapshot_time = utcnow_naive()
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=3,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=2), open=69950.0, high=70040.0, low=69920.0, close=70000.0, volume=900.0),
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=1), open=70000.0, high=70070.0, low=69980.0, close=70030.0, volume=980.0),
            MarketCandle(timestamp=snapshot_time, open=70030.0, high=70090.0, low=70010.0, close=70060.0, volume=1020.0),
        ],
        derivatives_context=DerivativesContextPayload(
            source="binance_public",
            spread_bps=2.0,
            spread_stress_score=0.2,
        ),
    )
    feature_payload = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.32,
        volatility_pct=0.002,
        volume_ratio=1.08,
        drawdown_pct=0.001,
        rsi=58.0,
        atr=90.0,
        atr_pct=0.0013,
        momentum_score=0.18,
        multi_timeframe={
            "15m": TimeframeFeatureContext(
                timeframe="15m",
                trend_score=0.32,
                volatility_pct=0.002,
                volume_ratio=1.08,
                drawdown_pct=0.001,
                rsi=58.0,
                atr=90.0,
                atr_pct=0.0013,
                momentum_score=0.18,
            )
        },
        regime=RegimeFeatureContext(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            volume_regime="normal",
            momentum_state="strengthening",
            weak_volume=False,
            momentum_weakening=False,
        ),
        data_quality_flags=[],
    )

    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": ["BTCUSDT"],
            "skipped_symbols": [],
            "rankings": [_ranking_candidate_payload(symbol="BTCUSDT", strategy_engine="breakout_exception_engine")],
        },
    )
    monkeypatch.setattr("trading_mvp.services.orchestrator.compute_features", lambda *args, **kwargs: feature_payload)

    class DummyGate:
        allowed = True
        reason = "allowed"

        def as_metadata(self) -> dict[str, object]:
            return {"allowed": True, "reason": "allowed"}

    class BreakoutProvider:
        name = "openai"

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            latest_price = float(payload["market_snapshot"]["latest_price"])
            return ProviderResult(
                provider="openai",
                output={
                    "decision": "long",
                    "confidence": 0.69,
                    "symbol": payload["market_snapshot"]["symbol"],
                    "timeframe": payload["market_snapshot"]["timeframe"],
                    "entry_zone_min": latest_price - 40.0,
                    "entry_zone_max": latest_price - 10.0,
                    "entry_mode": "breakout_confirm",
                    "holding_profile": "position",
                    "recommended_holding_profile": "position",
                    "invalidation_price": latest_price - 12.0,
                    "max_chase_bps": 8.0,
                    "idea_ttl_minutes": 20,
                    "stop_loss": latest_price - 12.0,
                    "take_profit": latest_price + 16.0,
                    "max_holding_minutes": 120,
                    "risk_pct": 0.01,
                    "leverage": 1.5,
                    "rationale_codes": ["BREAKOUT_EXCEPTION_ALLOWED"],
                    "explanation_short": "breakout review",
                    "explanation_detailed": "The provider incorrectly tries to upgrade the holding profile, which must be bounded back to scalp.",
                },
            )

    monkeypatch.setattr("trading_mvp.services.orchestrator.get_openai_call_gate", lambda *args, **kwargs: DummyGate())
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_ai_prior_context",
        lambda *args, **kwargs: AIPriorContextPacket(
            engine_prior_available=True,
            engine_prior_sample_count=4,
            engine_sample_threshold_satisfied=True,
            engine_prior_classification="strong",
            capital_efficiency_available=True,
            capital_efficiency_sample_count=4,
            capital_efficiency_sample_threshold_satisfied=True,
            capital_efficiency_classification="efficient",
            prior_reason_codes=["ENGINE_PRIOR_STRONG", "CAPITAL_EFFICIENCY_EFFICIENT"],
            prior_penalty_level="none",
            expected_payoff_efficiency_hint_summary={"time_to_fail_hint_minutes": 22.0},
        ),
    )

    orchestrator = TradingOrchestrator(db_session)
    orchestrator.trading_agent = TradingDecisionAgent(BreakoutProvider())

    result = orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="manual",
        exchange_sync_checked=True,
        market_snapshot_override=snapshot,
        market_context_override={
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
        },
    )
    decision_row = db_session.get(AgentRun, result["decision_run_id"])

    assert decision_row is not None
    assert decision_row.output_payload["holding_profile"] == "scalp"
    assert decision_row.output_payload["recommended_holding_profile"] in {"scalp", "hold_current"}
    assert decision_row.metadata_json["prompt_family"] == "breakout_exception_review"
    assert decision_row.metadata_json["bounded_output_applied"] is True
    assert "INVALID_HOLDING_PROFILE_FOR_ENGINE" in decision_row.metadata_json["fallback_reason_codes"]
    assert decision_row.metadata_json["provider_status"] == "ok"
    assert decision_row.metadata_json["engine_prior_classification"] == "strong"
    assert decision_row.metadata_json["capital_efficiency_classification"] == "efficient"
    assert decision_row.metadata_json["prior_penalty_level"] == "none"


def test_pipeline_persists_quality_fail_closed_metadata_when_provider_not_called(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    snapshot_time = utcnow_naive()
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=3,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=2), open=69950.0, high=70040.0, low=69920.0, close=70000.0, volume=900.0),
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=1), open=70000.0, high=70070.0, low=69980.0, close=70030.0, volume=980.0),
            MarketCandle(timestamp=snapshot_time, open=70030.0, high=70090.0, low=70010.0, close=70060.0, volume=1020.0),
        ],
    )
    feature_payload = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.32,
        volatility_pct=0.002,
        volume_ratio=1.08,
        drawdown_pct=0.001,
        rsi=58.0,
        atr=90.0,
        atr_pct=0.0013,
        momentum_score=0.18,
        multi_timeframe={
            "15m": TimeframeFeatureContext(
                timeframe="15m",
                trend_score=0.32,
                volatility_pct=0.002,
                volume_ratio=1.08,
                drawdown_pct=0.001,
                rsi=58.0,
                atr=90.0,
                atr_pct=0.0013,
                momentum_score=0.18,
            )
        },
        regime=RegimeFeatureContext(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            volume_regime="normal",
            momentum_state="strengthening",
            weak_volume=False,
            momentum_weakening=False,
        ),
        data_quality_flags=["STALE_MARKET_DATA"],
    )

    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": ["BTCUSDT"],
            "skipped_symbols": [],
            "rankings": [_ranking_candidate_payload(symbol="BTCUSDT", strategy_engine="breakout_exception_engine")],
        },
    )
    monkeypatch.setattr("trading_mvp.services.orchestrator.compute_features", lambda *args, **kwargs: feature_payload)

    class DummyGate:
        allowed = True
        reason = "allowed"

        def as_metadata(self) -> dict[str, object]:
            return {"allowed": True, "reason": "allowed"}

    class ShouldNotRunProvider:
        name = "openai"

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            raise AssertionError("provider should not be called when degraded breakout quality is blocked")

    monkeypatch.setattr("trading_mvp.services.orchestrator.get_openai_call_gate", lambda *args, **kwargs: DummyGate())
    orchestrator = TradingOrchestrator(db_session)
    orchestrator.trading_agent = TradingDecisionAgent(ShouldNotRunProvider())

    result = orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="manual",
        exchange_sync_checked=True,
        market_snapshot_override=snapshot,
        market_context_override={
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
        },
    )
    decision_row = db_session.get(AgentRun, result["decision_run_id"])

    assert decision_row is not None
    assert decision_row.output_payload["decision"] == "hold"
    assert decision_row.output_payload["data_quality_fail_closed_applied"] is True
    assert decision_row.output_payload["provider_not_called_due_to_quality"] is True
    assert "BREAKOUT_QUALITY_INSUFFICIENT" in decision_row.output_payload["data_quality_block_reason_codes"]
    assert decision_row.metadata_json["provider_status"] == "quality_blocked"
    assert decision_row.metadata_json["data_quality_fail_closed_applied"] is True
    assert decision_row.metadata_json["provider_not_called_due_to_quality"] is True
    assert "BREAKOUT_QUALITY_INSUFFICIENT" in decision_row.metadata_json["data_quality_block_reason_codes"]


def test_scheduler_market_refresh_cycle_runs_without_ai_or_new_entry(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = False
    settings_row.tracked_symbols = ["BTCUSDT"]
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    higher_timeframe_context = {
        "15m": snapshot,
        "1h": build_market_snapshot("BTCUSDT", "1h", upto_index=140),
        "4h": build_market_snapshot("BTCUSDT", "4h", upto_index=140),
    }
    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_context", lambda **kwargs: higher_timeframe_context)

    result = run_market_refresh_cycle(db_session, triggered_by="scheduler")

    assert result["workflow"] == "market_refresh_cycle"
    assert result["results"][0]["status"] == "success"
    assert result["results"][0]["feature_snapshot_id"] is not None
    assert db_session.scalar(select(MarketSnapshot).limit(1)) is not None
    assert db_session.scalar(select(FeatureSnapshot).limit(1)) is not None
    assert db_session.scalar(select(AgentRun).limit(1)) is None
    assert db_session.scalar(select(Order).limit(1)) is None


def test_scheduler_position_management_cycle_does_not_create_new_entry(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.ai_provider = "mock"
    settings_row.position_management_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.flush()

    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70050.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=70000.0,
                high=70100.0,
                low=69900.0,
                close=70050.0,
                volume=1000.0,
            )
        ],
    )

    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda **kwargs: {
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
            "4h": snapshot.model_copy(update={"timeframe": "4h"}),
        },
    )
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.apply_position_management",
        lambda *args, **kwargs: {"status": "applied", "position_management_action": {"action": "tighten_stop"}},
    )

    from trading_mvp.models import Position

    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=69500.0,
            mark_price=70050.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=71000.0,
            metadata_json={},
        )
    )
    db_session.flush()

    result = run_position_management_cycle(db_session, triggered_by="scheduler")

    assert result["workflow"] == "position_management_cycle"
    assert result["results"][0]["status"] == "success"
    position_exit_review_run = db_session.scalar(select(AgentRun).limit(1))
    assert position_exit_review_run is not None
    assert position_exit_review_run.role == "position_exit_review"
    assert position_exit_review_run.output_payload["execution_boundary"] == "metadata_only_no_order_authority"
    position_row = db_session.scalar(select(Position).where(Position.symbol == "BTCUSDT"))
    assert position_row is not None
    assert position_row.metadata_json["position_exit_review"]["execution_boundary"] == "metadata_only_no_order_authority"
    assert position_row.metadata_json["position_exit_review_source"]["execution_boundary"] == "metadata_only_no_order_authority"
    assert db_session.scalar(select(RiskCheck).limit(1)) is None


def test_decision_cycle_skips_duplicate_same_candle_entry(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.live_trading_enabled = False
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.flush()

    snapshot_time = utcnow_naive()
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=snapshot_time,
                open=69900.0,
                high=70100.0,
                low=69800.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )
    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda **kwargs: {
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
            "4h": snapshot.model_copy(update={"timeframe": "4h"}),
        },
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_exchange_sync_cycle",
        lambda self, **kwargs: {"status": "ok", "symbols": ["BTCUSDT"]},
    )

    orchestrator = TradingOrchestrator(db_session)
    first = orchestrator.run_decision_cycle(trigger_event="manual", exchange_sync_checked=True)
    second = orchestrator.run_decision_cycle(trigger_event="manual", exchange_sync_checked=True)

    assert first["decision_run_id"] is not None
    assert second["status"] == "same_candle_skipped"
    assert second["decision_run_id"] is None


def test_symbol_due_uses_override_specific_interval(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT", "XRPUSDT"]
    settings_row.decision_cycle_interval_minutes = 15
    settings_row.symbol_cadence_overrides = [
        {"symbol": "BTCUSDT", "decision_cycle_interval_minutes_override": 5, "ai_call_interval_minutes_override": 10},
        {"symbol": "ETHUSDT", "decision_cycle_interval_minutes_override": 10, "ai_call_interval_minutes_override": 15},
        {"symbol": "XRPUSDT", "decision_cycle_interval_minutes_override": 30, "ai_call_interval_minutes_override": 30},
    ]
    now = utcnow_naive()
    db_session.add_all(
        [
            SchedulerRun(
                schedule_window="5m",
                workflow="interval_decision_cycle",
                status="success",
                triggered_by="scheduler",
                next_run_at=now - timedelta(minutes=1),
                outcome={"symbol": "BTCUSDT"},
            ),
            SchedulerRun(
                schedule_window="10m",
                workflow="interval_decision_cycle",
                status="success",
                triggered_by="scheduler",
                next_run_at=now + timedelta(minutes=3),
                outcome={"symbol": "ETHUSDT"},
            ),
            SchedulerRun(
                schedule_window="30m",
                workflow="interval_decision_cycle",
                status="success",
                triggered_by="scheduler",
                next_run_at=now + timedelta(minutes=20),
                outcome={"symbol": "XRPUSDT"},
            ),
        ]
    )
    db_session.flush()

    assert is_interval_decision_due(db_session, "BTCUSDT") is True
    assert is_interval_decision_due(db_session, "ETHUSDT") is False
    assert is_interval_decision_due(db_session, "XRPUSDT") is False


def test_idle_cadence_reduces_interval_decision_calls(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    now = utcnow_naive()
    db_session.add(
        SchedulerRun(
            schedule_window="15m",
            workflow="interval_decision_cycle",
            status="success",
            triggered_by="scheduler",
            created_at=now - timedelta(minutes=20),
            next_run_at=None,
            outcome={"symbol": "BTCUSDT"},
        )
    )
    db_session.flush()

    monkeypatch.setattr(
        TradingOrchestrator,
        "get_symbol_cadence_profile",
        lambda self, **kwargs: {
            "mode": "idle",
            "skip_reason": "RANGE_WEAK_VOLUME_NO_TRADE_ZONE",
            "ai_skipped_reason": "CADENCE_IDLE_NO_TRADE_ZONE",
            "reasons": ["RANGE_WEAK_VOLUME_NO_TRADE_ZONE"],
            "effective_cadence": {
                "market_refresh_interval_minutes": 2,
                "position_management_interval_seconds": 120,
                "decision_cycle_interval_minutes": 30,
                "ai_call_interval_minutes": 30,
                "entry_plan_watcher_interval_minutes": None,
            },
        },
    )

    assert is_interval_decision_due(db_session, "BTCUSDT") is False


def test_active_position_cadence_prioritizes_management(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    now = utcnow_naive()
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=70100.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            realized_pnl=0.0,
            unrealized_pnl=1.0,
            metadata_json={},
        )
    )
    db_session.add_all(
        [
            SchedulerRun(
                schedule_window="60s",
                workflow="position_management_cycle",
                status="success",
                triggered_by="scheduler",
                created_at=now - timedelta(seconds=40),
                next_run_at=None,
                outcome={"symbol": "BTCUSDT"},
            ),
            SchedulerRun(
                schedule_window="15m",
                workflow="interval_decision_cycle",
                status="success",
                triggered_by="scheduler",
                created_at=now - timedelta(minutes=10),
                next_run_at=None,
                outcome={"symbol": "BTCUSDT"},
            ),
        ]
    )
    db_session.flush()

    monkeypatch.setattr(
        TradingOrchestrator,
        "get_symbol_cadence_profile",
        lambda self, **kwargs: {
            "mode": "active_position",
            "skip_reason": "ACTIVE_POSITION_PRIORITY",
            "ai_skipped_reason": None,
            "reasons": ["ACTIVE_POSITION_PRIORITY"],
            "effective_cadence": {
                "market_refresh_interval_minutes": 2,
                "position_management_interval_seconds": 30,
                "decision_cycle_interval_minutes": 30,
                "ai_call_interval_minutes": 30,
                "entry_plan_watcher_interval_minutes": None,
            },
        },
    )

    from trading_mvp.services.scheduler import get_due_position_management_symbols

    assert get_due_position_management_symbols(db_session) == ["BTCUSDT"]
    assert is_interval_decision_due(db_session, "BTCUSDT") is False


def test_active_position_blocked_add_on_is_suppressed_until_state_changes(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.flush()

    snapshot_time = utcnow_naive()
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=70120.0,
        latest_volume=1200.0,
        candle_count=3,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=2), open=70020.0, high=70080.0, low=69980.0, close=70040.0, volume=1000.0),
            MarketCandle(timestamp=snapshot_time - timedelta(minutes=1), open=70040.0, high=70110.0, low=70010.0, close=70090.0, volume=1100.0),
            MarketCandle(timestamp=snapshot_time, open=70090.0, high=70150.0, low=70070.0, close=70120.0, volume=1200.0),
        ],
    )
    feature_payload = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.34,
        volatility_pct=0.002,
        volume_ratio=1.08,
        drawdown_pct=0.001,
        rsi=58.0,
        atr=90.0,
        atr_pct=0.0013,
        momentum_score=0.22,
        multi_timeframe={
            "15m": TimeframeFeatureContext(
                timeframe="15m",
                trend_score=0.34,
                volatility_pct=0.002,
                volume_ratio=1.08,
                drawdown_pct=0.001,
                rsi=58.0,
                atr=90.0,
                atr_pct=0.0013,
                momentum_score=0.22,
            )
        },
        regime=RegimeFeatureContext(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            volume_regime="strong",
            momentum_state="strengthening",
            weak_volume=False,
            momentum_weakening=False,
        ),
        data_quality_flags=[],
    )
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=70120.0,
            leverage=2.0,
            stop_loss=69400.0,
            take_profit=71200.0,
            realized_pnl=0.0,
            unrealized_pnl=12.0,
            metadata_json={},
        )
    )
    db_session.flush()

    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "capacity_reason": "mixed_breadth_moderate_capacity",
            "drawdown_capacity_reason": None,
            "drawdown_state": {},
            "selected_symbols": ["BTCUSDT"],
            "skipped_symbols": [],
            "rankings": [_ranking_candidate_payload(symbol="BTCUSDT", strategy_engine="trend_pullback_engine")],
        },
    )
    monkeypatch.setattr("trading_mvp.services.orchestrator.compute_features", lambda *args, **kwargs: feature_payload)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_position_management_context",
        lambda *args, **kwargs: {
            "holding_profile": "scalp",
            "holding_profile_reason": "active_position_test",
            "hard_stop_active": True,
            "stop_widening_allowed": False,
            "current_r_multiple": 0.8,
            "break_even_eligible": True,
            "tightened_stop_loss": 70020.0,
            "reduce_reason_codes": [],
        },
    )

    class DummyGate:
        allowed = True
        reason = "allowed"

        def as_metadata(self) -> dict[str, object]:
            return {"allowed": True, "reason": "allowed"}

    monkeypatch.setattr("trading_mvp.services.orchestrator.get_openai_call_gate", lambda *args, **kwargs: DummyGate())
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_ai_prior_context",
        lambda *args, **kwargs: AIPriorContextPacket(),
    )

    captured_ai_contexts: list[dict[str, object]] = []

    def fake_run(_market_snapshot, _feature_payload, _open_positions, _risk_context, *, use_ai, **kwargs):
        ai_context = kwargs.get("ai_context")
        if ai_context is not None and hasattr(ai_context, "model_dump"):
            captured_ai_contexts.append(ai_context.model_dump(mode="json"))
        return (
            TradeDecision(
                decision="long",
                confidence=0.62,
                symbol="BTCUSDT",
                timeframe="15m",
                entry_zone_min=70080.0,
                entry_zone_max=70100.0,
                entry_mode="pullback_confirm",
                invalidation_price=69400.0,
                max_chase_bps=12.0,
                idea_ttl_minutes=20,
                stop_loss=69400.0,
                take_profit=71200.0,
                max_holding_minutes=120,
                risk_pct=0.01,
                leverage=1.0,
                rationale_codes=["TEST_ADD_ON"],
                explanation_short="same-side add-on",
                explanation_detailed="Test helper tries to reuse the same-side add-on route.",
            ),
            "openai",
            {"source": "llm", "bounded_output_applied": True},
        )

    def fake_evaluate_risk(session, settings_row, decision, market_snapshot, **kwargs):
        blocked = decision.decision == "long"
        reason_codes = ["LARGEST_POSITION_LIMIT_REACHED"] if blocked else []
        result = RiskCheckResult(
            allowed=not blocked,
            decision=decision.decision,
            reason_codes=reason_codes,
            blocked_reason_codes=reason_codes,
            adjustment_reason_codes=[],
            approved_risk_pct=0.0 if blocked else float(decision.risk_pct or 0.0),
            approved_leverage=0.0 if blocked else float(decision.leverage or 0.0),
            raw_projected_notional=0.0,
            approved_notional=0.0,
            approved_projected_notional=0.0,
            approved_qty=None,
            approved_quantity=None,
            operating_mode="hold" if blocked else "live",
            effective_leverage_cap=5.0,
            symbol_risk_tier="btc",
            exposure_metrics={},
        )
        row = RiskCheck(
            symbol=decision.symbol,
            decision_run_id=kwargs.get("decision_run_id"),
            market_snapshot_id=kwargs.get("market_snapshot_id"),
            allowed=result.allowed,
            decision=decision.decision,
            reason_codes=list(result.reason_codes),
            approved_risk_pct=result.approved_risk_pct,
            approved_leverage=result.approved_leverage,
            payload=result.model_dump(mode="json"),
        )
        session.add(row)
        session.flush()
        return result, row

    monkeypatch.setattr("trading_mvp.services.orchestrator.evaluate_risk", fake_evaluate_risk)

    orchestrator = TradingOrchestrator(db_session)
    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_run)

    first = orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="realtime_cycle",
        exchange_sync_checked=True,
        market_snapshot_override=snapshot,
        market_context_override={
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
        },
    )
    second = orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="realtime_cycle",
        exchange_sync_checked=True,
        market_snapshot_override=snapshot,
        market_context_override={
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
        },
    )

    first_decision_row = db_session.get(AgentRun, first["decision_run_id"])
    second_decision_row = db_session.get(AgentRun, second["decision_run_id"])

    assert first_decision_row is not None
    assert second_decision_row is not None
    assert first["risk_result"]["reason_codes"] == ["LARGEST_POSITION_LIMIT_REACHED"]
    assert captured_ai_contexts[0]["strategy_engine_context"]["allow_same_side_add_on"] is True
    assert captured_ai_contexts[0]["strategy_engine_context"]["allowed_add_on_side"] == "long"
    assert captured_ai_contexts[1]["strategy_engine_context"]["allow_same_side_add_on"] is False
    assert captured_ai_contexts[1]["strategy_engine_context"]["entry_proposal_suppression_active"] is True
    assert captured_ai_contexts[1]["strategy_engine_context"]["entry_proposal_suppressed_reason_code"] == "LARGEST_POSITION_LIMIT_REACHED"
    assert second_decision_row.metadata_json["active_position_prompt_route_context"]["allow_same_side_add_on"] is False
    assert second_decision_row.metadata_json["active_position_prompt_route_context"]["entry_proposal_suppression_active"] is True
    second_audit_events = {
        event.event_type: event
        for event in db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == str(second["decision_run_id"]),
                AuditEvent.event_type.in_(("agent_output", "decision_ai_invoked", "decision_ai_bounded", "decision_cycle_completed")),
            )
        )
    }
    assert second_audit_events["agent_output"].payload["suppression_active"] is True
    assert second_audit_events["agent_output"].payload["suppression_reason_code"] == "LARGEST_POSITION_LIMIT_REACHED"
    assert second_audit_events["agent_output"].payload["allow_same_side_add_on"] is False
    assert second_audit_events["agent_output"].payload["allowed_add_on_side"] is None
    assert second_audit_events["decision_ai_invoked"].payload["suppression_active"] is True
    assert second_audit_events["decision_ai_invoked"].payload["suppression_reason_code"] == "LARGEST_POSITION_LIMIT_REACHED"
    assert second_audit_events["decision_ai_invoked"].payload["allow_same_side_add_on"] is False
    assert second_audit_events["decision_ai_invoked"].payload["allowed_add_on_side"] is None
    assert second_audit_events["decision_ai_bounded"].payload["suppression_active"] is True
    assert second_audit_events["decision_ai_bounded"].payload["suppression_reason_code"] == "LARGEST_POSITION_LIMIT_REACHED"
    assert second_audit_events["decision_ai_bounded"].payload["allow_same_side_add_on"] is False
    assert second_audit_events["decision_ai_bounded"].payload["allowed_add_on_side"] is None
    assert second_audit_events["decision_cycle_completed"].payload["suppression_active"] is True
    assert second_audit_events["decision_cycle_completed"].payload["suppression_reason_code"] == "LARGEST_POSITION_LIMIT_REACHED"
    assert second_audit_events["decision_cycle_completed"].payload["allow_same_side_add_on"] is False
    assert second_audit_events["decision_cycle_completed"].payload["allowed_add_on_side"] is None


def test_run_decision_cycle_allows_ai_in_idle_no_trade_zone(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()
    _mark_pipeline_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=3,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(timestamp=utcnow_naive() - timedelta(minutes=2), open=70010.0, high=70040.0, low=69970.0, close=70000.0, volume=800.0),
            MarketCandle(timestamp=utcnow_naive() - timedelta(minutes=1), open=70000.0, high=70020.0, low=69980.0, close=69995.0, volume=760.0),
            MarketCandle(timestamp=utcnow_naive(), open=69995.0, high=70015.0, low=69985.0, close=70000.0, volume=720.0),
        ],
    )
    feature_payload = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.02,
        volatility_pct=0.001,
        volume_ratio=0.6,
        drawdown_pct=0.001,
        rsi=50.0,
        atr=80.0,
        atr_pct=0.0011,
        momentum_score=0.01,
        multi_timeframe={
            "15m": TimeframeFeatureContext(
                timeframe="15m",
                trend_score=0.02,
                volatility_pct=0.001,
                volume_ratio=0.6,
                drawdown_pct=0.001,
                rsi=50.0,
                atr=80.0,
                atr_pct=0.0011,
                momentum_score=0.01,
            )
        },
        regime=RegimeFeatureContext(
            primary_regime="range",
            trend_alignment="range",
            volatility_regime="normal",
            volume_regime="weak",
            momentum_state="weakening",
            weak_volume=True,
            momentum_weakening=True,
        ),
        data_quality_flags=["WEAK_VOLUME", "MOMENTUM_WEAKENING"],
    )

    class DummyGate:
        allowed = True
        reason = "allowed"

        def as_metadata(self) -> dict[str, object]:
            return {"allowed": True, "reason": "allowed"}

    captured_use_ai: list[bool] = []

    def fake_run(*args, use_ai, **kwargs):
        captured_use_ai.append(use_ai)
        return (
            TradeDecision(
                decision="hold",
                confidence=0.42,
                symbol="BTCUSDT",
                timeframe="15m",
                entry_zone_min=None,
                entry_zone_max=None,
                entry_mode="none",
                invalidation_price=None,
                max_chase_bps=None,
                idea_ttl_minutes=None,
                stop_loss=None,
                take_profit=None,
                max_holding_minutes=60,
                risk_pct=0.01,
                leverage=1.0,
                rationale_codes=["CADENCE_TEST"],
                explanation_short="idle cadence hold",
                explanation_detailed="no-trade zone is passed as AI context while risk remains deterministic",
            ),
            "deterministic-mock",
            {},
        )

    monkeypatch.setattr("trading_mvp.services.orchestrator.compute_features", lambda *args, **kwargs: feature_payload)
    monkeypatch.setattr("trading_mvp.services.orchestrator.get_openai_call_gate", lambda *args, **kwargs: DummyGate())

    orchestrator = TradingOrchestrator(db_session)
    monkeypatch.setattr(orchestrator.trading_agent, "run", fake_run)

    result = orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="realtime_cycle",
        exchange_sync_checked=True,
        market_snapshot_override=snapshot,
        market_context_override={"15m": snapshot, "1h": snapshot.model_copy(update={"timeframe": "1h"})},
    )

    assert captured_use_ai == [True]
    assert result["cadence"]["mode"] == "idle"
    assert result["skip_reason"] == "RANGE_WEAK_VOLUME_NO_TRADE_ZONE"
    assert result["ai_skipped_reason"] is None


def test_cadence_profile_enters_idle_when_setup_disable_cooldown_is_active(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.adaptive_signal_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    _seed_setup_cluster_history(
        db_session,
        net_pnls=[-28.0, -22.0, -18.0, -14.0],
        signed_slippage_bps=[16.0, 15.0, 14.0, 13.0],
        entry_mode="pullback_confirm",
        primary_regime="bullish",
        trend_alignment="bullish_aligned",
    )

    feature_payload = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.48,
        volatility_pct=0.003,
        volume_ratio=1.15,
        drawdown_pct=0.001,
        rsi=58.0,
        atr=90.0,
        atr_pct=0.0013,
        momentum_score=0.22,
        multi_timeframe={
            "15m": TimeframeFeatureContext(
                timeframe="15m",
                trend_score=0.48,
                volatility_pct=0.003,
                volume_ratio=1.15,
                drawdown_pct=0.001,
                rsi=58.0,
                atr=90.0,
                atr_pct=0.0013,
                momentum_score=0.22,
            )
        },
        regime=RegimeFeatureContext(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            volume_regime="normal",
            momentum_state="strengthening",
            weak_volume=False,
            momentum_weakening=False,
        ),
        data_quality_flags=[],
    )

    profile = TradingOrchestrator(db_session).get_symbol_cadence_profile(
        symbol="BTCUSDT",
        timeframe="15m",
        feature_payload=feature_payload,
    )

    assert profile["mode"] == "idle"
    assert profile["skip_reason"] == "SETUP_DISABLE_COOLDOWN_ACTIVE"
    assert profile["ai_skipped_reason"] == "CADENCE_IDLE_SETUP_DISABLE_ACTIVE"
    assert "SETUP_DISABLE_COOLDOWN_ACTIVE" in profile["reasons"]
    assert profile["setup_disable_active"] is True


def test_cadence_profile_keeps_active_position_priority_over_idle_skip_reasons(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.adaptive_signal_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    _seed_setup_cluster_history(
        db_session,
        net_pnls=[-28.0, -22.0, -18.0, -14.0],
        signed_slippage_bps=[16.0, 15.0, 14.0, 13.0],
        entry_mode="pullback_confirm",
        primary_regime="bullish",
        trend_alignment="bullish_aligned",
    )

    feature_payload = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.48,
        volatility_pct=0.003,
        volume_ratio=1.15,
        drawdown_pct=0.001,
        rsi=58.0,
        atr=90.0,
        atr_pct=0.0013,
        momentum_score=0.22,
        multi_timeframe={},
        regime=RegimeFeatureContext(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            volume_regime="normal",
            momentum_state="strengthening",
            weak_volume=False,
            momentum_weakening=False,
        ),
        data_quality_flags=[],
    )

    profile = TradingOrchestrator(db_session).get_symbol_cadence_profile(
        symbol="BTCUSDT",
        timeframe="15m",
        feature_payload=feature_payload,
        open_positions=[object()],
        armed_plans=[],
    )

    assert profile["mode"] == "active_position"
    assert profile["skip_reason"] == "ACTIVE_POSITION_PRIORITY"
    assert profile["ai_skipped_reason"] is None
    assert profile["setup_disable_active"] is False


def test_cadence_profile_uses_holding_profile_overlay_for_open_position(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    settings_row.symbol_cadence_overrides = [
        {
            "symbol": "BTCUSDT",
            "decision_cycle_interval_minutes_override": 5,
            "ai_call_interval_minutes_override": 5,
            "position_management_interval_seconds_override": 15,
        }
    ]
    db_session.add(settings_row)
    db_session.flush()

    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=70250.0,
        leverage=2.0,
        stop_loss=69400.0,
        take_profit=71200.0,
        unrealized_pnl=2.5,
        metadata_json={
            "position_management": {
                "holding_profile": "position",
                "holding_profile_reason": "strong_structural_regime_position_allowed",
                "hard_stop_active": True,
                "stop_widening_allowed": False,
            }
        },
    )
    db_session.add(position)
    db_session.flush()

    feature_payload = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "regime": {
            "primary_regime": "bullish",
            "trend_alignment": "bullish_aligned",
            "volatility_regime": "normal",
            "volume_regime": "normal",
            "momentum_state": "strengthening",
        },
    }

    profile = TradingOrchestrator(db_session).get_symbol_cadence_profile(
        symbol="BTCUSDT",
        timeframe="15m",
        feature_payload=feature_payload,
        open_positions=[position],
        armed_plans=[],
    )

    assert profile["mode"] == "active_position"
    assert profile["active_holding_profile"] == "position"
    assert profile["active_holding_profile_reason"] == "strong_structural_regime_position_allowed"
    assert profile["holding_profile_cadence_source"] == "open_position"
    assert profile["effective_cadence"]["position_management_interval_seconds"] == 60
    assert profile["effective_cadence"]["decision_cycle_interval_minutes"] == 30
    assert profile["effective_cadence"]["ai_call_interval_minutes"] == 30


def test_cadence_profile_keeps_armed_entry_plan_priority_over_idle_skip_reasons(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.adaptive_signal_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    _seed_setup_cluster_history(
        db_session,
        net_pnls=[-28.0, -22.0, -18.0, -14.0],
        signed_slippage_bps=[16.0, 15.0, 14.0, 13.0],
        entry_mode="pullback_confirm",
        primary_regime="bullish",
        trend_alignment="bullish_aligned",
    )

    feature_payload = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.48,
        volatility_pct=0.003,
        volume_ratio=1.15,
        drawdown_pct=0.001,
        rsi=58.0,
        atr=90.0,
        atr_pct=0.0013,
        momentum_score=0.22,
        multi_timeframe={},
        regime=RegimeFeatureContext(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            volume_regime="normal",
            momentum_state="strengthening",
            weak_volume=False,
            momentum_weakening=False,
        ),
        data_quality_flags=[],
    )

    profile = TradingOrchestrator(db_session).get_symbol_cadence_profile(
        symbol="BTCUSDT",
        timeframe="15m",
        feature_payload=feature_payload,
        armed_plans=[object()],
    )

    assert profile["mode"] == "armed_entry_plan"
    assert profile["skip_reason"] == "ARMED_ENTRY_PLAN_ACTIVE"
    assert profile["ai_skipped_reason"] is None
    assert profile["effective_cadence"]["entry_plan_watcher_interval_minutes"] == 1
