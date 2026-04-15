from __future__ import annotations

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from trading_mvp.enums import AgentRole, TriggerEvent
from trading_mvp.models import (
    AgentRun,
    Alert,
    CompetitorNote,
    MarketSnapshot,
    PnLSnapshot,
    ProductBacklog,
    RiskCheck,
    SchedulerRun,
    SystemHealthEvent,
    UIFeedback,
)
from trading_mvp.providers import build_model_provider
from trading_mvp.schemas import MarketSnapshotPayload
from trading_mvp.services.account import (
    account_snapshot_to_dict,
    get_latest_pnl_snapshot,
    get_open_positions,
)
from trading_mvp.services.agents import (
    ChiefReviewAgent,
    IntegrationPlannerAgent,
    ProductImprovementAgent,
    TradingDecisionAgent,
    UIUXAgent,
    persist_agent_run,
)
from trading_mvp.services.ai_usage import get_openai_call_gate
from trading_mvp.services.adaptive_signal import build_adaptive_signal_context
from trading_mvp.services.audit import create_alert, record_audit_event, record_health_event
from trading_mvp.services.backlog_insights import (
    build_signal_performance_report,
    build_structured_competitor_notes,
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
from trading_mvp.services.runtime_state import summarize_runtime_state
from trading_mvp.services.settings import (
    get_effective_symbols,
    get_or_create_settings,
    get_runtime_credentials,
    serialize_settings,
)
from trading_mvp.time_utils import utcnow_naive


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
        self.product_agent = ProductImprovementAgent(provider)

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


    def _latest_alerts(self, limit: int = 5) -> list[Alert]:
        return list(self.session.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(limit)))


    def _latest_health_events(self, limit: int = 10) -> list[SystemHealthEvent]:
        return list(self.session.scalars(select(SystemHealthEvent).order_by(desc(SystemHealthEvent.created_at)).limit(limit)))

    def run_exchange_sync_cycle(
        self,
        *,
        symbol: str | None = None,
        trigger_event: str = "background_poll",
    ) -> dict[str, object]:
        if not self.credentials.binance_api_key or not self.credentials.binance_api_secret:
            return {
                "status": "skipped",
                "reason": "LIVE_CREDENTIALS_MISSING",
                "symbol": symbol or self.settings_row.default_symbol,
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

    def run_market_refresh_cycle(
        self,
        *,
        timeframe: str | None = None,
        upto_index: int | None = None,
        force_stale: bool = False,
        status: str = "market_refresh",
        trigger_event: str = TriggerEvent.MANUAL.value,
        auto_resume_checked: bool = False,
    ) -> dict[str, object]:
        auto_resume_result = self._ensure_auto_resume(
            trigger_event=trigger_event,
            auto_resume_checked=auto_resume_checked,
        )
        timeframe = timeframe or self.settings_row.default_timeframe
        exchange_sync_result: dict[str, object] | None = None
        if self._should_poll_exchange_state(trigger_event):
            exchange_sync_result = self.run_exchange_sync_cycle(trigger_event=trigger_event)
        symbols = get_effective_symbols(self.settings_row)
        results: list[dict[str, object]] = []
        for symbol in symbols:
            market_snapshot, market_row = self._collect_market_snapshot(
                symbol=symbol,
                timeframe=timeframe,
                upto_index=upto_index,
                force_stale=force_stale,
            )
            results.append(
                {
                    "symbol": symbol,
                    "market_snapshot_id": market_row.id,
                    "snapshot_time": market_snapshot.snapshot_time.isoformat(),
                    "latest_price": market_snapshot.latest_price,
                    "status": status,
                }
            )
        return {
            "symbols": symbols,
            "cycles": len(results),
            "mode": status,
            "results": results,
            "account": self._account_snapshot_preview(),
            "settings": serialize_settings(self.settings_row),
            "auto_resume": auto_resume_result,
            "exchange_sync": exchange_sync_result,
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
    ) -> dict[str, object]:
        auto_resume_result = self._ensure_auto_resume(
            trigger_event=trigger_event,
            auto_resume_checked=auto_resume_checked,
        )
        symbol = (symbol or self.settings_row.default_symbol).upper()
        timeframe = timeframe or self.settings_row.default_timeframe
        exchange_sync_result: dict[str, object] | None = None
        if self._should_poll_exchange_state(trigger_event) and not exchange_sync_checked:
            exchange_sync_result = self.run_exchange_sync_cycle(symbol=symbol, trigger_event=trigger_event)
        market_snapshot, market_row = self._collect_market_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
        )
        market_context = build_market_context(
            symbol=symbol,
            base_timeframe=timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
            use_binance=self.settings_row.binance_market_data_enabled,
            binance_testnet_enabled=self.settings_row.binance_testnet_enabled,
            stale_threshold_seconds=self.settings_row.stale_market_seconds,
        )
        higher_timeframe_context = {
            tf: payload for tf, payload in market_context.items() if tf != timeframe
        }
        if not self.settings_row.ai_enabled:
            return {
                "symbol": symbol,
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
        if open_positions and self._should_execute_live(trigger_event):
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
            "analysis_context": _decision_analysis_context(feature_payload),
            "position_management": position_management_result or {"position_management_context": position_management_context},
        }
        decision_run = persist_agent_run(
            self.session,
            AgentRole.TRADING_DECISION,
            trigger_event,
            {
                "market_snapshot": market_snapshot.model_dump(mode="json"),
                "market_context": {
                    context_timeframe: snapshot.model_dump(mode="json")
                    for context_timeframe, snapshot in higher_timeframe_context.items()
                },
                "features": feature_payload.model_dump(mode="json"),
                "risk_context": risk_context,
            },
            decision,
            provider_name=provider_name,
            metadata_json=decision_metadata,
        )
        record_audit_event(self.session, event_type="agent_output", entity_type="agent_run", entity_id=str(decision_run.id), message="Trading decision generated.", payload={"provider": provider_name, "decision": decision.model_dump(mode="json")})
        risk_result, risk_row = evaluate_risk(
            self.session,
            self.settings_row,
            decision,
            market_snapshot,
            decision_run_id=decision_run.id,
            market_snapshot_id=market_row.id,
            execution_mode="historical_replay" if trigger_event == "historical_replay" else "live",
        )
        record_audit_event(self.session, event_type="risk_check", entity_type="risk_check", entity_id=str(risk_row.id), severity="warning" if not risk_result.allowed else "info", message="Risk check completed.", payload=risk_result.model_dump(mode="json"))

        execution_result: dict[str, object] | None = None
        if risk_result.allowed and decision.decision != "hold" and self._should_execute_live(trigger_event):
            execution_result = execute_live_trade(
                self.session,
                self.settings_row,
                decision_run_id=decision_run.id,
                decision=decision,
                market_snapshot=market_snapshot,
                risk_result=risk_result,
                risk_row=risk_row,
            )
        elif risk_result.allowed and decision.decision != "hold":
            record_audit_event(
                self.session,
                event_type="live_execution_skipped",
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="info",
                message="Live execution skipped for non-live trigger.",
                payload={"trigger_event": trigger_event, "symbol": symbol},
            )
        elif not risk_result.allowed:
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
            "market_snapshot_id": market_row.id,
            "feature_snapshot_id": feature_row.id,
            "decision_run_id": decision_run.id,
            "risk_check_id": risk_row.id,
            "chief_review_run_id": chief_run.id,
            "decision": decision.model_dump(mode="json"),
            "risk_result": risk_result.model_dump(mode="json"),
            "execution": execution_result,
            "logic_variant": logic_variant,
            "account": account_snapshot_to_dict(get_latest_pnl_snapshot(self.session, self.settings_row)),
            "settings": serialize_settings(self.settings_row),
            "auto_resume": auto_resume_result,
            "exchange_sync": exchange_sync_result,
        }


    def run_selected_symbols_cycle(
        self,
        *,
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
        exchange_sync_result: dict[str, object] | None = None
        if self._should_poll_exchange_state(trigger_event):
            exchange_sync_result = self.run_exchange_sync_cycle(trigger_event=trigger_event)
        symbols = get_effective_symbols(self.settings_row)
        results: list[dict[str, object]] = []
        failed_symbols: list[str] = []
        for symbol in symbols:
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
                        exchange_sync_checked=exchange_sync_result is not None,
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
            "symbols": symbols,
            "cycles": len(results),
            "mode": "market_data_only" if not self.settings_row.ai_enabled else "ai_active",
            "failed_symbols": failed_symbols,
            "logic_variant": logic_variant,
            "results": results,
            "account": self._account_snapshot_preview() if not self.settings_row.ai_enabled else account_snapshot_to_dict(get_latest_pnl_snapshot(self.session, self.settings_row)),
            "settings": serialize_settings(self.settings_row),
            "auto_resume": auto_resume_result,
            "exchange_sync": exchange_sync_result,
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


    def run_product_review(self, triggered_by: str = TriggerEvent.SCHEDULED.value) -> dict[str, object]:
        if not self.settings_row.ai_enabled:
            return {"status": "skipped", "reason": "AI_DISABLED"}
        openai_gate = get_openai_call_gate(
            self.session,
            self.settings_row,
            AgentRole.PRODUCT_IMPROVEMENT.value,
            triggered_by,
            has_openai_key=bool(self.credentials.openai_api_key),
        )
        competitor_notes = list(self.session.scalars(select(CompetitorNote).order_by(desc(CompetitorNote.created_at)).limit(20)))
        structured_competitor_notes = build_structured_competitor_notes(self.session)
        signal_report = build_signal_performance_report(self.session)
        existing_titles = [title for title in self.session.scalars(select(ProductBacklog.title))]
        latest_pnl = get_latest_pnl_snapshot(self.session, self.settings_row)
        kpi_summary = {
            "equity": latest_pnl.equity,
            "daily_pnl": latest_pnl.daily_pnl,
            "cumulative_pnl": latest_pnl.cumulative_pnl,
            "consecutive_losses": latest_pnl.consecutive_losses,
            "tracked_symbols": get_effective_symbols(self.settings_row),
        }
        output, provider_name, metadata = self.product_agent.run(
            kpi_summary,
            competitor_notes,
            signal_report.model_dump(mode="json"),
            structured_competitor_notes.model_dump(mode="json"),
            existing_titles,
            use_ai=openai_gate.allowed,
        )
        metadata = {**metadata, "gate": openai_gate.as_metadata()}
        run = persist_agent_run(
            self.session,
            AgentRole.PRODUCT_IMPROVEMENT,
            triggered_by,
            {
                "kpi_summary": kpi_summary,
                "competitor_note_count": len(competitor_notes),
                "signal_performance_report": signal_report.model_dump(mode="json"),
                "structured_competitor_notes": structured_competitor_notes.model_dump(mode="json"),
            },
            output,
            provider_name=provider_name,
            metadata_json=metadata,
        )
        created_titles: list[str] = []
        for item in output.items:
            if item.title in existing_titles:
                continue
            self.session.add(ProductBacklog(title=item.title, problem=item.problem, proposal=item.proposal, severity=item.severity, effort=item.effort, impact=item.impact, priority=item.priority, rationale=item.rationale, source="product_improvement_agent", status="open"))
            created_titles.append(item.title)
        return {"agent_run_id": run.id, "created_titles": created_titles, "items": output.model_dump(mode="json")}
