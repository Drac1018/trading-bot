from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from trading_mvp.enums import AgentRole, OperatingMode, PriorityLevel
from trading_mvp.models import (
    AgentRun,
    Alert,
    CompetitorNote,
    Position,
    SystemHealthEvent,
    UIFeedback,
)
from trading_mvp.providers import ProviderResult, StructuredModelProvider
from trading_mvp.schemas import (
    AgentRunRecord,
    ChiefReviewSummary,
    FeaturePayload,
    IntegrationSuggestion,
    IntegrationSuggestionBatch,
    MarketSnapshotPayload,
    ProductBacklogBatch,
    ProductBacklogItem,
    RiskCheckResult,
    TradeDecision,
    UXSuggestion,
    UXSuggestionBatch,
)
from trading_mvp.time_utils import utcnow_naive


def _summary_from_output(output: BaseModel | dict[str, Any]) -> str:
    payload = output.model_dump(mode="json") if isinstance(output, BaseModel) else output
    for key in ("summary", "explanation_short", "title", "recommended_mode"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    items = payload.get("items")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict) and "title" in first:
            return str(first["title"])
    return "agent_run"


def persist_agent_run(
    session: Session,
    role: AgentRole,
    trigger_event: str,
    input_payload: dict[str, Any],
    output: BaseModel | dict[str, Any],
    *,
    provider_name: str = "deterministic-mock",
    metadata_json: dict[str, Any] | None = None,
    schema_valid: bool = True,
    status: str = "completed",
) -> AgentRun:
    now = utcnow_naive()
    metadata = metadata_json or {}
    derived_status = status
    source = metadata.get("source")
    gate = metadata.get("gate")
    if status == "completed":
        if source == "llm_fallback":
            derived_status = "fallback"
        elif isinstance(gate, dict) and gate.get("allowed") is False:
            derived_status = "skipped"
    output_payload = output.model_dump(mode="json") if isinstance(output, BaseModel) else output
    row = AgentRun(
        role=role.value,
        trigger_event=trigger_event,
        schema_name=output.__class__.__name__ if isinstance(output, BaseModel) else "dict",
        status=derived_status,
        provider_name=provider_name,
        summary=_summary_from_output(output),
        input_payload=input_payload,
        output_payload=output_payload,
        metadata_json=metadata,
        schema_valid=schema_valid,
        started_at=now,
        completed_at=now,
    )
    session.add(row)
    session.flush()
    return row


def serialize_agent_run(row: AgentRun) -> AgentRunRecord:
    return AgentRunRecord(
        role=row.role,
        trigger_event=row.trigger_event,
        schema_name=row.schema_name,
        status=row.status,
        provider_name=row.provider_name,
        summary=row.summary,
        input_payload=row.input_payload,
        output_payload=row.output_payload,
        metadata_json=row.metadata_json,
        schema_valid=row.schema_valid,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _provider_metadata(result: ProviderResult | None, *, source: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"source": source}
    if result is None:
        return metadata
    if result.usage is not None:
        metadata["usage"] = result.usage
    if result.request_id:
        metadata["request_id"] = result.request_id
    return metadata


class TradingDecisionAgent:
    def __init__(self, provider: StructuredModelProvider) -> None:
        self.provider = provider

    @staticmethod
    def _adaptive_brackets(
        side: Literal["long", "short"],
        *,
        price: float,
        atr: float,
        features: FeaturePayload,
    ) -> tuple[float, float]:
        safe_price = max(price, 1.0)
        safe_atr = max(atr, safe_price * 0.002)
        stop_multiple = 1.1
        take_multiple = 2.0

        if features.regime.primary_regime == "range":
            stop_multiple *= 0.85
            take_multiple *= 0.75
        elif features.regime.trend_alignment in {"bullish_aligned", "bearish_aligned"}:
            take_multiple *= 1.25

        if features.regime.volatility_regime == "expanded":
            stop_multiple *= 1.15
            take_multiple *= 1.2
        elif features.regime.volatility_regime == "compressed":
            stop_multiple *= 0.9

        if features.regime.weak_volume:
            take_multiple *= 0.85
        if features.regime.momentum_state == "weakening":
            take_multiple *= 0.9
        if features.regime.momentum_state == "overextended":
            stop_multiple *= 0.95
            take_multiple *= 0.8

        if side == "long":
            return round(safe_price - safe_atr * stop_multiple, 2), round(safe_price + safe_atr * take_multiple, 2)
        return round(safe_price + safe_atr * stop_multiple, 2), round(safe_price - safe_atr * take_multiple, 2)

    @staticmethod
    def _confidence(features: FeaturePayload) -> float:
        confidence = 0.22 + min(abs(features.trend_score) / 2.5, 0.32)
        confidence += min(abs(features.momentum_score) / 3.0, 0.18)
        if features.regime.trend_alignment in {"bullish_aligned", "bearish_aligned"}:
            confidence += 0.12
        if features.regime.volume_regime == "strong":
            confidence += 0.05
        if features.regime.primary_regime == "range":
            confidence -= 0.1
        if features.regime.weak_volume:
            confidence -= 0.08
        if "STALE_MARKET_DATA" in features.data_quality_flags or "INCOMPLETE_MARKET_DATA" in features.data_quality_flags:
            confidence -= 0.2
        return round(min(0.96, max(0.18, confidence)), 4)

    def _deterministic_decision(
        self,
        market_snapshot: MarketSnapshotPayload,
        features: FeaturePayload,
        open_positions: list[Position],
        risk_context: dict[str, Any],
    ) -> TradeDecision:
        price = market_snapshot.latest_price
        atr = max(features.atr, price * 0.0025)
        confidence = self._confidence(features)
        open_position = open_positions[0] if open_positions else None

        decision: Literal["hold", "long", "short", "reduce", "exit"] = "hold"
        rationale = ["NO_EDGE"]
        short_explanation = "추세 우위가 충분하지 않아 관망이 우선입니다."
        detailed_explanation = (
            "현재 신호는 중립에 가깝고 리스크 대비 기대수익이 제한적이라 신규 진입보다 "
            "다음 평가 사이클까지 모니터링이 안전합니다."
        )

        regime_name = features.regime.primary_regime
        trend_alignment = features.regime.trend_alignment
        weak_volume = features.regime.weak_volume
        momentum_weakening = features.regime.momentum_weakening
        range_like_signal = regime_name == "range"
        long_signal = (
            trend_alignment == "bullish_aligned"
            and regime_name != "range"
            and features.trend_score >= 0.22
            and features.momentum_score >= 0.12
            and 45.0 <= features.rsi <= 82.0
            and not weak_volume
        )
        short_signal = (
            trend_alignment == "bearish_aligned"
            and regime_name != "range"
            and features.trend_score <= -0.22
            and features.momentum_score <= -0.12
            and 18.0 <= features.rsi <= 55.0
            and not weak_volume
        )
        weakening_signal = momentum_weakening or weak_volume or range_like_signal
        operating_state = str(risk_context.get("operating_state", "TRADABLE"))

        if open_position is not None and operating_state == "PROTECTION_REQUIRED":
            decision = "long" if open_position.side == "long" else "short"
            rationale = ["PROTECTION_REQUIRED", "RESTORE_PROTECTION"]
            short_explanation = "누락된 보호 주문을 복구할 수 있도록 손절가와 익절가를 다시 제안합니다."
            detailed_explanation = (
                "현재는 신규 진입보다 기존 포지션 보호 복구가 우선입니다. "
                "기존 포지션 방향을 유지한 채 손절가와 익절가를 다시 설정하도록 보수적으로 판단합니다."
            )
        elif open_position is not None and operating_state == "DEGRADED_MANAGE_ONLY":
            decision = "reduce"
            rationale = ["MANAGE_ONLY_MODE", "REDUCE_EXPOSURE"]
            short_explanation = "관리 전용 상태이므로 기존 포지션을 일부 축소하는 판단을 우선합니다."
            detailed_explanation = (
                "보호 복구가 반복 실패해 신규 진입은 막힌 상태입니다. "
                "현재는 노출을 줄이고 남은 포지션만 보수적으로 관리하는 것이 우선입니다."
            )

        if decision == "hold" and open_position is not None and open_position.side == "long" and (short_signal or features.rsi > 73):
            decision = "exit"
            rationale = ["LONG_EXHAUSTION", "POSITION_RISK_RESET"]
            short_explanation = "기존 롱 포지션의 우위가 약해져 청산이 우선입니다."
            detailed_explanation = "과열 또는 반전 신호가 확인돼 기존 롱 포지션을 정리하는 편이 보수적입니다."
        elif decision == "hold" and open_position is not None and open_position.side == "short" and (long_signal or features.rsi < 28):
            decision = "exit"
            rationale = ["SHORT_EXHAUSTION", "POSITION_RISK_RESET"]
            short_explanation = "기존 숏 포지션의 우위가 약해져 청산이 우선입니다."
            detailed_explanation = "반등 또는 추세 전환 가능성이 커져 기존 숏 포지션을 정리하는 편이 보수적입니다."
        elif decision == "hold" and open_position is not None and weakening_signal:
            decision = "reduce"
            rationale = ["WEAKENING_SIGNAL", "PROTECT_OPEN_PNL"]
            short_explanation = "기존 포지션을 일부 축소해 리스크를 낮춥니다."
            detailed_explanation = "추세 강도와 거래량 우위가 약화돼 포지션 규모를 줄이는 편이 안전합니다."
        elif decision == "hold" and long_signal:
            decision = "long"
            rationale = ["TREND_UP", "VOLUME_SUPPORT", "RSI_HEALTHY"]
            short_explanation = "상승 추세와 거래량 지지가 확인돼 롱 진입을 제안합니다."
            detailed_explanation = (
                "단기 추세 점수와 RSI, 거래량 지지가 함께 개선돼 리스크 대비 기대수익이 "
                "상대적으로 양호한 구간으로 판단합니다."
            )
        elif decision == "hold" and short_signal:
            decision = "short"
            rationale = ["TREND_DOWN", "VOLUME_SUPPORT", "RSI_WEAK"]
            short_explanation = "하락 추세가 우세해 숏 진입을 제안합니다."
            detailed_explanation = (
                "추세 점수와 거래량 우위가 모두 약세를 가리켜 단기 숏 시나리오가 "
                "상대적으로 우세한 구간으로 판단합니다."
            )

        risk_pct = max(0.003, round(confidence * 0.008, 4))
        if features.regime.volatility_regime == "expanded":
            risk_pct *= 0.85
        if weak_volume or range_like_signal:
            risk_pct *= 0.85
        risk_pct = min(float(risk_context["max_risk_per_trade"]), round(risk_pct, 4))

        leverage = max(1.0, round(1.0 + (confidence * 1.6), 2))
        if features.regime.volatility_regime == "expanded":
            leverage *= 0.85
        if weak_volume or range_like_signal:
            leverage *= 0.9
        leverage = min(float(risk_context["max_leverage"]), round(leverage, 2))

        entry_band = atr * (0.08 if range_like_signal else 0.14)
        entry_min = round(price - entry_band, 2)
        entry_max = round(price + entry_band, 2)
        stop_loss: float | None = None
        take_profit: float | None = None
        if decision == "long":
            stop_loss, take_profit = self._adaptive_brackets("long", price=price, atr=atr, features=features)
        elif decision == "short":
            stop_loss, take_profit = self._adaptive_brackets("short", price=price, atr=atr, features=features)
        elif decision in {"reduce", "exit"} and open_position is not None:
            stop_loss = open_position.stop_loss
            take_profit = open_position.take_profit

        return TradeDecision(
            decision=decision,
            confidence=round(confidence, 4),
            symbol=market_snapshot.symbol,
            timeframe=market_snapshot.timeframe,
            entry_zone_min=float(entry_min),
            entry_zone_max=float(entry_max),
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_holding_minutes=240,
            risk_pct=float(risk_pct),
            leverage=float(leverage),
            rationale_codes=rationale,
            explanation_short=short_explanation,
            explanation_detailed=detailed_explanation,
        )

    def run(
        self,
        market_snapshot: MarketSnapshotPayload,
        features: FeaturePayload,
        open_positions: list[Position],
        risk_context: dict[str, Any],
        *,
        use_ai: bool,
        max_input_candles: int,
    ) -> tuple[TradeDecision, str, dict[str, Any]]:
        baseline = self._deterministic_decision(market_snapshot, features, open_positions, risk_context)
        if not use_ai:
            decision = baseline.model_copy(
                update={"rationale_codes": baseline.rationale_codes + ["PROVIDER_DETERMINISTIC_MOCK"]}
            )
            return decision, "deterministic-mock", {"source": "deterministic"}

        provider_result: ProviderResult | None = None
        try:
            candle_limit = max(8, min(max_input_candles, 16))
            compact_candles = [
                {
                    "t": candle.timestamp.isoformat(),
                    "o": round(candle.open, 2),
                    "h": round(candle.high, 2),
                    "l": round(candle.low, 2),
                    "c": round(candle.close, 2),
                    "v": round(candle.volume, 2),
                }
                for candle in market_snapshot.candles[-candle_limit:]
            ]
            compact_payload = {
                "market_snapshot": {
                    "symbol": market_snapshot.symbol,
                    "timeframe": market_snapshot.timeframe,
                    "latest_price": market_snapshot.latest_price,
                    "latest_volume": market_snapshot.latest_volume,
                    "is_stale": market_snapshot.is_stale,
                    "is_complete": market_snapshot.is_complete,
                    "candles": compact_candles,
                },
                "features": {
                    "trend_score": features.trend_score,
                    "volatility_pct": features.volatility_pct,
                    "volume_ratio": features.volume_ratio,
                    "drawdown_pct": features.drawdown_pct,
                    "rsi": features.rsi,
                    "atr": features.atr,
                    "atr_pct": features.atr_pct,
                    "momentum_score": features.momentum_score,
                    "regime": features.regime.model_dump(mode="json"),
                    "multi_timeframe": {
                        timeframe: context.model_dump(mode="json")
                        for timeframe, context in features.multi_timeframe.items()
                    },
                    "data_quality_flags": features.data_quality_flags,
                },
                "open_positions": [
                    {
                        "side": position.side,
                        "quantity": position.quantity,
                        "entry_price": position.entry_price,
                        "stop_loss": position.stop_loss,
                        "take_profit": position.take_profit,
                    }
                    for position in open_positions
                ],
                "risk_context": risk_context,
                "deterministic_baseline": {
                    "decision": baseline.decision,
                    "confidence": baseline.confidence,
                    "entry_zone_min": baseline.entry_zone_min,
                    "entry_zone_max": baseline.entry_zone_max,
                    "stop_loss": baseline.stop_loss,
                    "take_profit": baseline.take_profit,
                    "risk_pct": baseline.risk_pct,
                    "leverage": baseline.leverage,
                    "rationale_codes": baseline.rationale_codes,
                    "explanation_short": baseline.explanation_short,
                },
            }
            provider_result = self.provider.generate(
                AgentRole.TRADING_DECISION.value,
                compact_payload,
                response_model=TradeDecision,
                instructions=(
                    "You are the trading decision role inside a risk-controlled live trading system. "
                    "Return one structured decision. Stay conservative and concise. "
                    "Do not exceed the provided leverage or risk context. "
                    "If confidence is weak, return hold. Keep explanation_short brief and explanation_detailed under 3 sentences."
                ),
            )
            decision = TradeDecision.model_validate(provider_result.output)
            decision = decision.model_copy(
                update={"rationale_codes": decision.rationale_codes + [f"PROVIDER_{provider_result.provider.upper()}"]}
            )
            return decision, provider_result.provider, _provider_metadata(provider_result, source="llm")
        except Exception as exc:
            decision = baseline.model_copy(
                update={
                    "rationale_codes": baseline.rationale_codes
                    + ["LLM_FALLBACK", f"PROVIDER_{self.provider.name.upper()}"]
                }
            )
            metadata = _provider_metadata(provider_result, source="llm_fallback")
            metadata["error"] = str(exc)
            return decision, "deterministic-mock", metadata


class ChiefReviewAgent:
    def __init__(self, provider: StructuredModelProvider | None = None) -> None:
        self.provider = provider

    def _deterministic_review(
        self,
        decision: TradeDecision,
        risk_result: RiskCheckResult,
        health_events: list[SystemHealthEvent],
        alerts: list[Alert],
    ) -> ChiefReviewSummary:
        blockers = list(risk_result.reason_codes)
        blockers.extend(alert.title for alert in alerts[:3])
        degraded = any(event.status not in {"ok", "healthy"} for event in health_events[:5])

        if not risk_result.allowed or degraded:
            mode = OperatingMode.HOLD
            priority = PriorityLevel.HIGH if degraded else PriorityLevel.MEDIUM
            summary = "리스크 또는 시스템 상태 때문에 실행보다 HOLD가 우선입니다."
            must_do = ["차단 사유 확인", "시스템 상태 점검", "다음 평가 전까지 모니터링"]
        elif decision.decision == "hold":
            mode = OperatingMode.MONITOR
            priority = PriorityLevel.MEDIUM
            summary = "시장 신호가 약해 모니터링 유지가 적절합니다."
            must_do = ["다음 평가 대기", "거래량과 추세 변화 확인"]
        else:
            mode = OperatingMode.ACT
            priority = PriorityLevel.MEDIUM
            summary = "리스크 검증을 통과해 종이매매 기준 실행 가능한 상태입니다."
            must_do = ["실행 결과 모니터링", "후속 알림 확인"]

        return ChiefReviewSummary(
            summary=summary,
            recommended_mode=mode.value,
            must_do_actions=must_do,
            blockers=blockers,
            priority=priority.value,
        )

    def run(
        self,
        decision: TradeDecision,
        risk_result: RiskCheckResult,
        health_events: list[SystemHealthEvent],
        alerts: list[Alert],
        *,
        use_ai: bool,
    ) -> tuple[ChiefReviewSummary, str, dict[str, Any]]:
        baseline = self._deterministic_review(decision, risk_result, health_events, alerts)
        if not use_ai or self.provider is None:
            return baseline, "deterministic-mock", {"source": "deterministic"}
        try:
            provider_result = self.provider.generate(
                AgentRole.CHIEF_REVIEW.value,
                {
                    "decision": decision.model_dump(mode="json"),
                    "risk_result": risk_result.model_dump(mode="json"),
                    "health_events": [
                        {"component": event.component, "status": event.status, "message": event.message}
                        for event in health_events[:8]
                    ],
                    "alerts": [{"title": alert.title, "message": alert.message} for alert in alerts[:5]],
                    "deterministic_baseline": baseline.model_dump(mode="json"),
                },
                response_model=ChiefReviewSummary,
                instructions=(
                    "Summarize the current operating posture. "
                    "If risk_result.allowed is false, recommended_mode should remain hold."
                ),
            )
            result = ChiefReviewSummary.model_validate(provider_result.output)
            return result, provider_result.provider, _provider_metadata(provider_result, source="llm")
        except Exception as exc:
            return baseline, "deterministic-mock", {"source": "llm_fallback", "error": str(exc)}


class IntegrationPlannerAgent:
    def __init__(self, provider: StructuredModelProvider) -> None:
        self.provider = provider

    def _deterministic_output(
        self,
        metrics_summary: dict[str, Any],
        health_events: list[SystemHealthEvent],
    ) -> IntegrationSuggestionBatch:
        issues = [event.message for event in health_events if event.status not in {"ok", "healthy"}]
        items = [
            IntegrationSuggestion(
                title="리스크 차단 사유 집계 자동화",
                integration_point="scheduler -> risk_checks -> alerts",
                description="차단 사유 상위 패턴을 정리해 운영 병목을 빠르게 찾습니다.",
                automation_opportunity="4시간마다 차단 사유 상위 3개 요약 생성",
                tech_debt_item="차단 및 실행 흐름의 집계 지표 보강",
                priority="high",
            ),
            IntegrationSuggestion(
                title="실행 슬리피지 추적 카드 강화",
                integration_point="executions -> dashboard overview",
                description="종이매매 체결 품질을 실거래 전환 전에 더 빠르게 확인합니다.",
                automation_opportunity="슬리피지 상한 초과 시 즉시 경고 생성",
                tech_debt_item="실행 지표의 상단 요약 카드 부족",
                priority="medium",
            ),
        ]
        if issues:
            items.append(
                IntegrationSuggestion(
                    title="시스템 상태 이벤트 기반 복구 가이드 연결",
                    integration_point="system_health_events -> agents",
                    description="문제 이벤트가 발생하면 관련 운영 점검 항목을 바로 보여줍니다.",
                    automation_opportunity="이상 이벤트별 점검 체크리스트 자동 첨부",
                    tech_debt_item="장애 대응 문맥 링크 부족",
                    priority="high",
                )
            )
        return IntegrationSuggestionBatch(items=items)

    def run(
        self,
        metrics_summary: dict[str, Any],
        health_events: list[SystemHealthEvent],
        *,
        use_ai: bool,
    ) -> tuple[IntegrationSuggestionBatch, str, dict[str, Any]]:
        baseline = self._deterministic_output(metrics_summary, health_events)
        if not use_ai:
            return baseline, "deterministic-mock", {"source": "deterministic"}
        try:
            provider_result = self.provider.generate(
                AgentRole.INTEGRATION_PLANNER.value,
                {
                    "metrics_summary": metrics_summary,
                    "health_events": [
                        {"component": event.component, "status": event.status, "message": event.message}
                        for event in health_events[:10]
                    ],
                    "deterministic_baseline": baseline.model_dump(mode="json"),
                },
                response_model=IntegrationSuggestionBatch,
                instructions="Return 2 to 4 actionable integration suggestions. Prioritize observability and automation.",
            )
            result = IntegrationSuggestionBatch.model_validate(provider_result.output)
            return result, provider_result.provider, _provider_metadata(provider_result, source="llm")
        except Exception as exc:
            return baseline, "deterministic-mock", {"source": "llm_fallback", "error": str(exc)}


class UIUXAgent:
    def __init__(self, provider: StructuredModelProvider) -> None:
        self.provider = provider

    def _deterministic_output(self, feedback_rows: list[UIFeedback]) -> UXSuggestionBatch:
        pages = {row.page for row in feedback_rows}
        items = [
            UXSuggestion(
                page="overview",
                title="추천과 승인 실행의 시각적 분리 강화",
                suggestion="AI 추천과 리스크 승인 결과를 더 명확히 분리해 오판을 줄입니다.",
                severity="high",
                improved_copy="AI 추천은 참고 정보이며 실제 실행은 리스크 엔진 승인 후에만 반영됩니다.",
            ),
            UXSuggestion(
                page="risk",
                title="HOLD 사유 상단 요약 카드 추가",
                suggestion="차단 사유를 페이지 상단에서 바로 읽을 수 있게 요약합니다.",
                severity="medium",
                improved_copy="현재 HOLD 상태입니다. 차단 사유를 먼저 해소한 뒤 다음 평가를 기다려 주세요.",
            ),
        ]
        if "settings" in pages:
            items.append(
                UXSuggestion(
                    page="settings",
                    title="연결 테스트 결과 요약 문구 보강",
                    suggestion="API 키 저장 여부와 마지막 연결 결과를 한눈에 보여줍니다.",
                    severity="medium",
                    improved_copy="저장된 키는 마스킹 처리되며, 연결 테스트는 현재 입력값 또는 저장값 기준으로 실행됩니다.",
                )
            )
        return UXSuggestionBatch(items=items)

    def run(self, feedback_rows: list[UIFeedback], *, use_ai: bool) -> tuple[UXSuggestionBatch, str, dict[str, Any]]:
        baseline = self._deterministic_output(feedback_rows)
        if not use_ai:
            return baseline, "deterministic-mock", {"source": "deterministic"}
        try:
            provider_result = self.provider.generate(
                AgentRole.UI_UX.value,
                {
                    "feedback_rows": [
                        {
                            "page": row.page,
                            "sentiment": row.sentiment,
                            "feedback": row.feedback,
                        }
                        for row in feedback_rows[:12]
                    ],
                    "deterministic_baseline": baseline.model_dump(mode="json"),
                },
                response_model=UXSuggestionBatch,
                instructions="Return concise UI/UX suggestions for an internal trading dashboard. No deployment actions.",
            )
            result = UXSuggestionBatch.model_validate(provider_result.output)
            return result, provider_result.provider, _provider_metadata(provider_result, source="llm")
        except Exception as exc:
            return baseline, "deterministic-mock", {"source": "llm_fallback", "error": str(exc)}


class ProductImprovementAgent:
    def __init__(self, provider: StructuredModelProvider) -> None:
        self.provider = provider

    def _deterministic_output(
        self,
        competitor_notes: list[CompetitorNote],
        existing_backlog_titles: list[str],
    ) -> ProductBacklogBatch:
        items = [
            ProductBacklogItem(
                title="시그널 성과 분해 리포트 추가",
                problem="어떤 신호 조합이 성과를 만들었는지 즉시 파악하기 어렵습니다.",
                proposal="rationale code 기준 성과 분해 리포트를 24시간 리뷰에 포함합니다.",
                severity="medium",
                effort="medium",
                impact="high",
                priority="high",
                rationale="운영자가 어떤 신호를 신뢰해야 하는지 더 빨리 판단할 수 있습니다.",
            ),
            ProductBacklogItem(
                title="경쟁사 메모 구조화",
                problem="경쟁사 메모가 자유 형식이라 반복 분석과 비교가 어렵습니다.",
                proposal="기능 카테고리와 차별점 기준으로 메모 구조를 통일합니다.",
                severity="low",
                effort="small",
                impact="medium",
                priority="medium",
                rationale="제품 개선 근거를 더 안정적으로 축적할 수 있습니다.",
            ),
        ]
        if competitor_notes and "실행 슬리피지 리포트" not in existing_backlog_titles:
            items.append(
                ProductBacklogItem(
                    title="실행 슬리피지 리포트",
                    problem="체결 슬리피지 변화를 운영 화면에서 연속적으로 보기 어렵습니다.",
                    proposal="주간 슬리피지 요약 리포트와 임계치 경보를 추가합니다.",
                    severity="medium",
                    effort="medium",
                    impact="high",
                    priority="high",
                    rationale="실거래 전환 이전에 실행 품질 리스크를 더 빨리 확인할 수 있습니다.",
                )
            )
        return ProductBacklogBatch(items=items)

    def run(
        self,
        kpi_summary: dict[str, Any],
        competitor_notes: list[CompetitorNote],
        signal_performance_report: dict[str, Any],
        structured_competitor_notes: dict[str, Any],
        existing_backlog_titles: list[str],
        *,
        use_ai: bool,
    ) -> tuple[ProductBacklogBatch, str, dict[str, Any]]:
        baseline = self._deterministic_output(competitor_notes, existing_backlog_titles)
        if not use_ai:
            return baseline, "deterministic-mock", {"source": "deterministic"}
        try:
            provider_result = self.provider.generate(
                AgentRole.PRODUCT_IMPROVEMENT.value,
                {
                    "kpi_summary": kpi_summary,
                    "competitor_notes": [note.note for note in competitor_notes[:10]],
                    "signal_performance_report": signal_performance_report,
                    "structured_competitor_notes": structured_competitor_notes,
                    "existing_backlog_titles": existing_backlog_titles[:20],
                    "deterministic_baseline": baseline.model_dump(mode="json"),
                },
                response_model=ProductBacklogBatch,
                instructions="Return product improvement backlog items for a trading operations MVP. Do not change trading policy automatically.",
            )
            result = ProductBacklogBatch.model_validate(provider_result.output)
            return result, provider_result.provider, _provider_metadata(provider_result, source="llm")
        except Exception as exc:
            return baseline, "deterministic-mock", {"source": "llm_fallback", "error": str(exc)}
