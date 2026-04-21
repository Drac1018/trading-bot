import {
  describeEventSourceProvenance,
  describeAlignmentStatus,
  describeEffectivePolicyPreview,
  describeEnforcementMode,
  describeEventBias,
  describeEventReasonCode,
  describeImportance,
  describeManualWindowFlags,
  describePolicySource,
  describeRiskState,
  describeSourceStatus,
  describeSourceStatusHelp,
  describeWindowScope,
  formatUtcTimestamp,
  inferEventSourceProvenance,
  normalizeSourceStatus,
  summarizeEntryPolicy,
  summarizeReasonCodes,
  toneForAlignment,
  toneForPolicyPreview,
  toneForSourceStatus,
  type EventOperatorControlPayload,
} from "./event-operator-control.js";

export type OperatorDetailTone = "good" | "warn" | "danger" | "neutral";

export type OperatorDetailItem = {
  label: string;
  value: string;
  hint: string;
};

export type OperatorDetailAlert = {
  tone: OperatorDetailTone;
  text: string;
};

export type OperatorDetailSection = {
  key:
    | "current_regime"
    | "derivatives_orderbook"
    | "upcoming_event_risk"
    | "ai_event_view"
    | "operator_event_view"
    | "alignment_result"
    | "effective_trading_policy_preview"
    | "manual_no_trade_window"
    | "risk_guard_decision"
    | "blocked_degraded_reason";
  title: string;
  tone: OperatorDetailTone;
  items: OperatorDetailItem[];
  alerts: OperatorDetailAlert[];
};

export type OperatorDetailSymbolLike = {
  market_context_summary: Record<string, unknown>;
  derivatives_summary?: Record<string, unknown>;
  event_context_summary?: Record<string, unknown>;
  event_operator_control?: EventOperatorControlPayload | null;
  ai_decision: {
    decision: string | null;
    confidence: number | null;
    event_risk_acknowledgement?: string | null;
    confidence_penalty_reason?: string | null;
    scenario_note?: string | null;
  };
  risk_guard: {
    allowed: boolean | null;
    decision: string | null;
    operating_state: string | null;
    approved_risk_pct: number | null;
    approved_leverage: number | null;
    blocked_reason_codes: string[];
    blocked_reason?: string | null;
    degraded_reason?: string | null;
    approval_required_reason?: string | null;
    policy_source?: string | null;
  };
  execution: {
    order_id: number | null;
    execution_status: string | null;
    order_status: string | null;
  };
  blocked_reasons: string[];
  stale_flags: string[];
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function unique(values: string[]) {
  return values.filter((item, index, array) => array.indexOf(item) === index);
}

function formatPercent(value: number | null) {
  if (value === null) {
    return "정보 없음";
  }
  return `${(value * 100).toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}%`;
}

function formatMaybeNumber(value: number | null, digits = 2) {
  if (value === null) {
    return "정보 없음";
  }
  return value.toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function formatExecutionState(execution: OperatorDetailSymbolLike["execution"], riskAllowed: boolean | null) {
  if (!execution.order_id) {
    return riskAllowed === false ? "진입 보류" : "주문 없음";
  }
  return execution.execution_status ?? execution.order_status ?? "진행 중";
}

function translateFlag(value: string) {
  const labels: Record<string, string> = {
    account: "계좌 정보가 늦게 들어오고 있습니다.",
    positions: "포지션 정보가 늦게 들어오고 있습니다.",
    open_orders: "주문 정보가 늦게 들어오고 있습니다.",
    protective_orders: "보호 주문 정보가 늦게 들어오고 있습니다.",
    market_snapshot: "시장 스냅샷이 늦게 갱신되고 있습니다.",
    market_snapshot_incomplete: "시장 스냅샷 일부가 비어 있습니다.",
    feature_input_missing: "판단에 필요한 일부 입력이 비어 있습니다.",
  };
  return labels[value] ?? value;
}

function fallbackAiBias(decision: string | null | undefined) {
  switch (decision) {
    case "long":
      return "bullish";
    case "short":
      return "bearish";
    case "hold":
    case "reduce":
    case "exit":
      return "no_trade";
    default:
      return "unknown";
  }
}

function fallbackAiRiskState(decision: string | null | undefined) {
  switch (decision) {
    case "long":
    case "short":
      return "risk_on";
    case "hold":
    case "reduce":
    case "exit":
      return "neutral";
    default:
      return "unknown";
  }
}

function toneForReasonCode(code: string): OperatorDetailTone {
  switch (code) {
    case "manual_no_trade_active":
    case "operator_force_no_trade":
    case "operator_bias_no_trade":
    case "alignment_conflict_block":
      return "danger";
    case "outside_valid_window":
    case "alignment_insufficient_data":
    case "ai_unavailable":
    case "ai_stale":
    case "ai_incomplete":
    case "operator_unavailable":
    case "event_context_stale":
    case "event_context_incomplete":
    case "event_context_unavailable":
      return "warn";
    default:
      return "neutral";
  }
}

function boolToKorean(value: boolean | null | undefined) {
  if (value == null) {
    return "정보 없음";
  }
  return value ? "예" : "아니오";
}

export function buildOperatorDetailSections(symbol: OperatorDetailSymbolLike): OperatorDetailSection[] {
  const regime = asRecord(symbol.market_context_summary);
  const derivatives = asRecord(symbol.derivatives_summary);
  const legacyEventContext = asRecord(symbol.event_context_summary);
  const eventControl = symbol.event_operator_control ?? null;
  const eventContext = asRecord(eventControl?.event_context ?? legacyEventContext);
  const operatorEventView = eventControl?.operator_event_view ?? null;
  const alignmentDecision = eventControl?.alignment_decision ?? null;
  const manualWindows = eventControl?.manual_no_trade_windows ?? [];
  const activeWindows = manualWindows.filter((window) => window.is_active);

  const blockedReasons = unique(
    symbol.risk_guard.blocked_reason_codes.length > 0
      ? symbol.risk_guard.blocked_reason_codes
      : symbol.blocked_reasons,
  );
  const degradedFlags = unique(symbol.stale_flags);

  const rawEventSourceStatus = asString(eventContext.source_status) ?? "unknown";
  const normalizedEventSourceStatus = normalizeSourceStatus(rawEventSourceStatus);
  const eventSourceProvenance = inferEventSourceProvenance({
    source_status: rawEventSourceStatus,
    source_provenance: asString(eventContext.source_provenance),
  });
  const rawAiSourceState = eventControl?.ai_event_view?.source_state ?? "unknown";
  const rawAlignmentStatus = alignmentDecision?.alignment_status ?? "insufficient_data";
  const rawEffectivePolicyPreview =
    eventControl?.effective_policy_preview ?? alignmentDecision?.effective_policy_preview ?? "insufficient_data";

  const aiBias = describeEventBias(eventControl?.ai_event_view?.ai_bias ?? fallbackAiBias(symbol.ai_decision.decision));
  const aiRiskState = describeRiskState(
    eventControl?.ai_event_view?.ai_risk_state ?? fallbackAiRiskState(symbol.ai_decision.decision),
  );
  const aiConfidence =
    typeof eventControl?.ai_event_view?.ai_confidence === "number"
      ? eventControl.ai_event_view.ai_confidence
      : symbol.ai_decision.confidence;
  const aiScenarioNote =
    eventControl?.ai_event_view?.scenario_note
    ?? symbol.ai_decision.scenario_note
    ?? symbol.ai_decision.event_risk_acknowledgement
    ?? "정보 없음";
  const aiPenaltyReason =
    eventControl?.ai_event_view?.confidence_penalty_reason
    ?? symbol.ai_decision.confidence_penalty_reason
    ?? "정보 없음";

  const nextEventName = asString(eventContext.next_event_name) ?? "정보 없음";
  const minutesToNextEvent = asNumber(eventContext.minutes_to_next_event);
  const activeRiskWindow = asBoolean(eventContext.active_risk_window) ?? false;

  const riskBlockedReason = symbol.risk_guard.blocked_reason ?? null;
  const riskApprovalRequiredReason = symbol.risk_guard.approval_required_reason ?? null;
  const riskDegradedReason = symbol.risk_guard.degraded_reason ?? null;
  const riskPolicySource = symbol.risk_guard.policy_source ?? "none";

  const policySummary = summarizeEntryPolicy({
    effectivePolicyPreview: rawEffectivePolicyPreview,
    blockedReason: riskBlockedReason ?? eventControl?.blocked_reason,
    approvalRequiredReason: riskApprovalRequiredReason ?? eventControl?.approval_required_reason,
    policySource: riskPolicySource,
  });

  const blockedAndDegradedAlerts: OperatorDetailAlert[] = [
    ...blockedReasons.map((code) => ({
      tone: code === riskApprovalRequiredReason ? "warn" as const : "danger" as const,
      text: describeEventReasonCode(code),
    })),
    ...degradedFlags.map((flag) => ({ tone: "warn" as const, text: translateFlag(flag) })),
  ];

  if (normalizedEventSourceStatus !== "available" && normalizedEventSourceStatus !== "unknown") {
    blockedAndDegradedAlerts.push({
      tone: toneForSourceStatus(rawEventSourceStatus),
      text: describeSourceStatusHelp(rawEventSourceStatus, {
        kind: "event_context",
        provenance: eventSourceProvenance,
      }),
    });
  }
  if (riskDegradedReason) {
    blockedAndDegradedAlerts.push({
      tone: "warn",
      text: describeEventReasonCode(riskDegradedReason),
    });
  }
  if (blockedAndDegradedAlerts.length === 0) {
    blockedAndDegradedAlerts.push({ tone: "neutral", text: "현재 막히거나 주의할 상태는 없습니다." });
  }

  const alignmentAlerts =
    alignmentDecision && alignmentDecision.reason_codes.length > 0
      ? alignmentDecision.reason_codes.map((code) => ({
          tone: toneForReasonCode(code),
          text: describeEventReasonCode(code),
        }))
      : [];

  return [
    {
      key: "current_regime",
      title: "현재 레짐",
      tone: "neutral",
      items: [
        { label: "주요 흐름", value: asString(regime.primary_regime) ?? "정보 없음", hint: "현재 시장 분위기 요약" },
        { label: "상위 흐름과의 방향", value: asString(regime.trend_alignment) ?? "정보 없음", hint: "큰 흐름과 같은 쪽인지 보여줍니다." },
        { label: "변동성", value: asString(regime.volatility_regime) ?? "정보 없음", hint: "가격 움직임이 거친지 차분한지 보여줍니다." },
        { label: "거래량", value: asString(regime.volume_regime) ?? "정보 없음", hint: "시장 참여 강도를 보여줍니다." },
        { label: "모멘텀", value: asString(regime.momentum_state) ?? "정보 없음", hint: "최근 탄력이 강해지는지 약해지는지 보여줍니다." },
      ],
      alerts: [],
    },
    {
      key: "derivatives_orderbook",
      title: "파생 / 오더북",
      tone: asBoolean(derivatives.available) ? "neutral" : "warn",
      items: [
        {
          label: "데이터 상태",
          value: asBoolean(derivatives.available) ? "정상" : "없음",
          hint: `데이터 출처: ${asString(derivatives.source) ?? "확인 중"}`,
        },
        { label: "펀딩 흐름", value: asString(derivatives.funding_bias) ?? "정보 없음", hint: "롱/숏 쏠림 압력을 간단히 보여줍니다." },
        { label: "베이시스 흐름", value: asString(derivatives.basis_bias) ?? "정보 없음", hint: "선물 쪽 분위기가 어느 방향인지 보여줍니다." },
        { label: "체결 흐름", value: asString(derivatives.taker_flow_alignment) ?? "정보 없음", hint: "공격적인 매수/매도 흐름을 요약합니다." },
        {
          label: "스프레드",
          value: asNumber(derivatives.spread_bps) === null ? "정보 없음" : `${formatMaybeNumber(asNumber(derivatives.spread_bps), 2)}bps`,
          hint: "호가 간격이 넓은지 확인합니다.",
        },
      ],
      alerts: [],
    },
    {
      key: "upcoming_event_risk",
      title: "예정 이벤트 리스크",
      tone: activeRiskWindow ? "danger" : toneForSourceStatus(rawEventSourceStatus),
      items: [
        { label: "다음 이벤트", value: nextEventName, hint: "가장 가까운 중요 일정입니다." },
        { label: "이벤트 시각", value: formatUtcTimestamp(asString(eventContext.next_event_at)), hint: "모든 시각은 UTC 기준입니다." },
        {
          label: "남은 시간",
          value: minutesToNextEvent === null ? "정보 없음" : `${minutesToNextEvent}분`,
          hint: "지금 시각을 기준으로 계산했습니다.",
        },
        { label: "중요도", value: describeImportance(asString(eventContext.next_event_importance)), hint: "이 일정이 시장에 줄 수 있는 영향 수준입니다." },
        {
          label: "위험 구간",
          value: activeRiskWindow ? "현재 주의 구간" : "현재는 아님",
          hint: asString(eventContext.summary_note) ?? "추가 설명 없음",
        },
        {
          label: "데이터 출처",
          value: describeEventSourceProvenance(eventSourceProvenance),
          hint: "실제 연결 데이터인지, 샘플/예시 데이터인지 알려줍니다.",
        },
        {
          label: "데이터 상태",
          value: describeSourceStatus(rawEventSourceStatus, { kind: "event_context" }),
          hint: `지연 여부: ${boolToKorean(asBoolean(eventContext.is_stale))} / 정보 완전성: ${boolToKorean(asBoolean(eventContext.is_complete))}`,
        },
      ],
      alerts:
        normalizedEventSourceStatus !== "available" && normalizedEventSourceStatus !== "unknown"
          ? [{
              tone: toneForSourceStatus(rawEventSourceStatus),
              text: describeSourceStatusHelp(rawEventSourceStatus, {
                kind: "event_context",
                provenance: eventSourceProvenance,
              }),
            }]
          : [],
    },
    {
      key: "ai_event_view",
      title: "AI 이벤트 뷰",
      tone: rawAiSourceState === "available" ? "neutral" : "warn",
      items: [
        { label: "AI 방향", value: aiBias, hint: "AI가 이벤트를 감안해 본 방향입니다." },
        { label: "AI 위험 판단", value: aiRiskState, hint: "AI가 본 현재 위험 수준입니다." },
        { label: "AI 신뢰도", value: aiConfidence === null ? "정보 없음" : aiConfidence.toFixed(2), hint: "AI 판단 확신도를 숫자로 보여줍니다." },
        {
          label: "AI 의견 상태",
          value: describeSourceStatus(rawAiSourceState, { kind: "ai_event_view" }),
          hint: "의견이 없거나 비어 있는 경우도 숨기지 않습니다.",
        },
        { label: "AI 메모", value: aiScenarioNote, hint: "AI가 남긴 짧은 상황 설명입니다." },
        { label: "신뢰도 조정 이유", value: aiPenaltyReason, hint: "AI가 신뢰도를 낮춘 이유가 있으면 보여줍니다." },
      ],
      alerts:
        rawAiSourceState === "available"
          ? []
          : [{ tone: "warn", text: "AI가 이벤트 관련 의견을 남기지 않았으면 그대로 미설정으로 표시합니다." }],
    },
    {
      key: "operator_event_view",
      title: "운영자 이벤트 뷰",
      tone: operatorEventView ? "neutral" : "warn",
      items: [
        { label: "운영자 방향", value: describeEventBias(operatorEventView?.operator_bias), hint: "운영자가 직접 정한 대응 방향입니다." },
        { label: "운영자 위험 판단", value: describeRiskState(operatorEventView?.operator_risk_state), hint: "운영자가 본 현재 위험 수준입니다." },
        {
          label: "적용 심볼",
          value: operatorEventView && operatorEventView.applies_to_symbols.length > 0 ? operatorEventView.applies_to_symbols.join(", ") : "전체 심볼",
          hint: "비워 두면 모든 심볼에 적용됩니다.",
        },
        { label: "영향 기간/관점", value: operatorEventView?.horizon ?? "정보 없음", hint: "예: 오늘 이벤트 전후, 이번 주 등으로 적습니다." },
        {
          label: "적용 시간",
          value: `${formatUtcTimestamp(operatorEventView?.valid_from)} ~ ${formatUtcTimestamp(operatorEventView?.valid_to)}`,
          hint: "모든 시각은 UTC 기준입니다.",
        },
        {
          label: "반영 방식",
          value: describeEnforcementMode(operatorEventView?.enforcement_mode),
          hint: operatorEventView?.note ?? "추가 메모 없음",
        },
      ],
      alerts:
        operatorEventView
          ? []
          : [{ tone: "warn", text: "운영자 이벤트 설정이 아직 없습니다. 이 경우 기존 AI와 리스크 기준으로만 움직입니다." }],
    },
    {
      key: "alignment_result",
      title: "정렬 결과",
      tone: toneForAlignment(rawAlignmentStatus),
      items: [
        { label: "비교 결과", value: describeAlignmentStatus(rawAlignmentStatus), hint: "AI 의견과 운영자 설정을 비교한 결과입니다." },
        {
          label: "핵심 이유",
          value: summarizeReasonCodes(alignmentDecision?.reason_codes),
          hint: "지금 결과가 나온 이유를 쉬운 문장으로 풉니다.",
        },
        { label: "평가 시각", value: formatUtcTimestamp(alignmentDecision?.evaluated_at), hint: "마지막으로 다시 계산한 시각입니다." },
        {
          label: "AI / 운영자 방향",
          value: `${aiBias} / ${describeEventBias(alignmentDecision?.operator_bias)}`,
          hint: `${aiRiskState} / ${describeRiskState(alignmentDecision?.operator_risk_state)}`,
        },
      ],
      alerts: alignmentAlerts,
    },
    {
      key: "effective_trading_policy_preview",
      title: "신규 진입 정책 미리보기",
      tone: toneForPolicyPreview(rawEffectivePolicyPreview),
      items: [
        { label: "신규 진입 한 줄 요약", value: policySummary, hint: "운영자가 가장 먼저 보면 되는 요약입니다." },
        { label: "현재 판단", value: describeEffectivePolicyPreview(rawEffectivePolicyPreview), hint: "지금 신규 진입을 어떻게 다루는지 보여줍니다." },
        { label: "판단 기준", value: describePolicySource(riskPolicySource), hint: "어떤 근거가 가장 크게 반영됐는지 보여줍니다." },
        {
          label: "적용 범위",
          value: "신규 진입에만 적용",
          hint: "청산·축소 같은 안전 조치는 계속 허용됩니다.",
        },
      ],
      alerts: [
        { tone: toneForPolicyPreview(rawEffectivePolicyPreview), text: "실제 신규 진입 판단도 같은 기준을 씁니다." },
      ],
    },
    {
      key: "manual_no_trade_window",
      title: "수동 노트레이드 윈도우",
      tone: activeWindows.length > 0 ? "danger" : "neutral",
      items: [
        { label: "현재 적용 중인 구간 수", value: String(activeWindows.length), hint: "하나라도 활성화되어 있으면 신규 진입 금지에 반영됩니다." },
        {
          label: "가장 최근 적용 범위",
          value: manualWindows[0] ? describeWindowScope(manualWindows[0].scope) : "없음",
          hint: manualWindows[0] ? `설정 ID: ${manualWindows[0].window_id}` : "저장된 설정 없음",
        },
        {
          label: "가장 최근 적용 시간",
          value: manualWindows[0] ? `${formatUtcTimestamp(manualWindows[0].start_at)} ~ ${formatUtcTimestamp(manualWindows[0].end_at)}` : "없음",
          hint: "모든 시각은 UTC 기준입니다.",
        },
        {
          label: "추가 옵션",
          value: manualWindows[0]
            ? describeManualWindowFlags(manualWindows[0].auto_resume, manualWindows[0].require_manual_rearm)
            : "없음",
          hint: manualWindows[0]?.reason ?? "사유 없음",
        },
      ],
      alerts:
        activeWindows.length > 0
          ? activeWindows.map((window) => ({
              tone: "danger" as const,
              text: `${window.window_id}: ${window.reason} (${formatUtcTimestamp(window.start_at)} ~ ${formatUtcTimestamp(window.end_at)})`,
            }))
          : [{ tone: "neutral", text: "현재 활성 수동 노트레이드 윈도우가 없습니다." }],
    },
    {
      key: "risk_guard_decision",
      title: "리스크 가드 판정",
      tone: symbol.risk_guard.allowed === false ? "danger" : symbol.risk_guard.allowed ? "good" : "neutral",
      items: [
        {
          label: "최종 결과",
          value: symbol.risk_guard.allowed === null ? "정보 없음" : symbol.risk_guard.allowed ? "허용" : "차단",
          hint: "신규 진입 직전에 거치는 마지막 안전 점검 결과입니다.",
        },
        { label: "판단 방향", value: symbol.risk_guard.decision ?? "정보 없음", hint: symbol.risk_guard.operating_state ?? "운영 상태 정보 없음" },
        { label: "허용 위험 비중", value: formatPercent(symbol.risk_guard.approved_risk_pct), hint: "이번 진입에 허용된 최대 위험 비중입니다." },
        { label: "허용 레버리지", value: symbol.risk_guard.approved_leverage === null ? "정보 없음" : `${formatMaybeNumber(symbol.risk_guard.approved_leverage, 1)}x`, hint: "이번 진입에 허용된 최대 레버리지입니다." },
        {
          label: "차단 사유",
          value: describeEventReasonCode(riskBlockedReason),
          hint: "신규 진입이 막힌 가장 직접적인 이유입니다.",
        },
        {
          label: "추가 확인 사유",
          value: describeEventReasonCode(riskApprovalRequiredReason),
          hint: "한 번 더 확인이 필요한 경우 그 이유를 보여줍니다.",
        },
        { label: "판단 기준", value: describePolicySource(riskPolicySource), hint: "어떤 근거가 이번 판단을 이끌었는지 보여줍니다." },
        { label: "실행 상태", value: formatExecutionState(symbol.execution, symbol.risk_guard.allowed), hint: "최근 주문/실행 상태를 함께 보여줍니다." },
      ],
      alerts: [],
    },
    {
      key: "blocked_degraded_reason",
      title: "차단 / 저하 상태",
      tone: blockedReasons.length > 0 ? "danger" : degradedFlags.length > 0 ? "warn" : "neutral",
      items: [
        {
          label: "현재 차단 이유",
          value: blockedReasons.length > 0 ? blockedReasons.map((code) => describeEventReasonCode(code)).join(" / ") : "없음",
          hint: "지금 신규 진입을 막고 있는 이유입니다.",
        },
        {
          label: "주의가 필요한 상태",
          value: degradedFlags.length > 0 ? degradedFlags.map((flag) => translateFlag(flag)).join(" / ") : "없음",
          hint: "데이터 지연이나 불완전 상태를 함께 보여줍니다.",
        },
      ],
      alerts: blockedAndDegradedAlerts,
    },
  ];
}
