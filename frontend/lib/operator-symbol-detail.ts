import {
  describeAlignmentStatus,
  describeEffectivePolicyPreview,
  describeEnforcementMode,
  describeEventBias,
  describeImportance,
  describeRiskState,
  describeSourceStatus,
  describeWindowScope,
  formatUtcTimestamp,
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
    return "unknown";
  }
  return `${(value * 100).toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}%`;
}

function formatMaybeNumber(value: number | null, digits = 2) {
  if (value === null) {
    return "unknown";
  }
  return value.toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function formatExecutionState(execution: OperatorDetailSymbolLike["execution"], riskAllowed: boolean | null) {
  if (!execution.order_id) {
    return riskAllowed === false ? "실행 없음" : "주문 없음";
  }
  return execution.execution_status ?? execution.order_status ?? "pending";
}

function translateFlag(value: string) {
  const labels: Record<string, string> = {
    account: "account stale",
    positions: "positions stale",
    open_orders: "open orders stale",
    protective_orders: "protective orders stale",
    market_snapshot: "market snapshot stale",
    market_snapshot_incomplete: "market snapshot incomplete",
    feature_input_missing: "feature input missing",
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

export function buildOperatorDetailSections(symbol: OperatorDetailSymbolLike): OperatorDetailSection[] {
  const regime = asRecord(symbol.market_context_summary);
  const derivatives = asRecord(symbol.derivatives_summary);
  const legacyEventContext = asRecord(symbol.event_context_summary);
  const eventControl = symbol.event_operator_control ?? null;
  const eventContext = asRecord(eventControl?.event_context ?? legacyEventContext);
  const operatorEventView = eventControl?.operator_event_view ?? null;
  const alignmentDecision = eventControl?.alignment_decision ?? null;
  const manualWindows = eventControl?.manual_no_trade_windows ?? [];
  const blockedReasons = unique(
    symbol.risk_guard.blocked_reason_codes.length > 0
      ? symbol.risk_guard.blocked_reason_codes
      : symbol.blocked_reasons,
  );
  const degradedFlags = unique(symbol.stale_flags);
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
    ?? "unknown";
  const aiPenaltyReason =
    eventControl?.ai_event_view?.confidence_penalty_reason
    ?? symbol.ai_decision.confidence_penalty_reason
    ?? "unknown";
  const aiSourceState = describeSourceStatus(eventControl?.ai_event_view?.source_state ?? "unknown");
  const eventSourceStatus = describeSourceStatus(asString(eventContext.source_status));
  const activeRiskWindow = asBoolean(eventContext.active_risk_window);
  const nextEventName = asString(eventContext.next_event_name) ?? "unknown";
  const nextEventTime = formatUtcTimestamp(asString(eventContext.next_event_at));
  const nextEventImportance = describeImportance(asString(eventContext.next_event_importance));
  const minutesToNextEvent = asNumber(eventContext.minutes_to_next_event);
  const effectivePolicyPreview = describeEffectivePolicyPreview(
    eventControl?.effective_policy_preview ?? alignmentDecision?.effective_policy_preview,
  );
  const activeWindows = manualWindows.filter((window) => window.is_active);
  const riskBlockedReason = symbol.risk_guard.blocked_reason ?? "none";
  const riskApprovalRequiredReason = symbol.risk_guard.approval_required_reason ?? "none";
  const riskDegradedReason = symbol.risk_guard.degraded_reason ?? "none";
  const riskPolicySource = symbol.risk_guard.policy_source ?? "none";

  const blockedAndDegradedAlerts: OperatorDetailAlert[] = [
    ...blockedReasons.map((code) => ({ tone: "danger" as const, text: `risk 차단: ${code}` })),
    ...degradedFlags.map((flag) => ({ tone: "warn" as const, text: `상태 저하: ${translateFlag(flag)}` })),
  ];
  if (eventSourceStatus !== "available" && eventSourceStatus !== "unknown") {
    blockedAndDegradedAlerts.push({
      tone: toneForSourceStatus(eventSourceStatus),
      text: `event source: ${eventSourceStatus}`,
    });
  }
  if (effectivePolicyPreview === "force_no_trade_window" || effectivePolicyPreview === "block_new_entries") {
    blockedAndDegradedAlerts.push({
      tone: toneForPolicyPreview(effectivePolicyPreview),
      text: `preview: ${effectivePolicyPreview} (entry semantics mirrored in risk_guard)`,
    });
  }
  if (riskDegradedReason !== "none") {
    blockedAndDegradedAlerts.push({ tone: "warn", text: `event policy degraded: ${riskDegradedReason}` });
  }
  if (blockedAndDegradedAlerts.length === 0) {
    blockedAndDegradedAlerts.push({ tone: "neutral", text: "현재 차단/저하 사유 없음" });
  }

  return [
    {
      key: "current_regime",
      title: "Current Regime",
      tone: "neutral",
      items: [
        { label: "primary_regime", value: asString(regime.primary_regime) ?? "unknown", hint: "descriptive market regime" },
        { label: "trend_alignment", value: asString(regime.trend_alignment) ?? "unknown", hint: "방향 정렬 상태" },
        { label: "volatility_regime", value: asString(regime.volatility_regime) ?? "unknown", hint: "변동성 상태" },
        { label: "volume_regime", value: asString(regime.volume_regime) ?? "unknown", hint: "거래량 참여도" },
        { label: "momentum_state", value: asString(regime.momentum_state) ?? "unknown", hint: "모멘텀 상태" },
      ],
      alerts: [],
    },
    {
      key: "derivatives_orderbook",
      title: "Derivatives / Orderbook",
      tone: asBoolean(derivatives.available) ? "neutral" : "warn",
      items: [
        { label: "available", value: String(asBoolean(derivatives.available) ?? false), hint: `source ${asString(derivatives.source) ?? "unknown"}` },
        { label: "funding_bias", value: asString(derivatives.funding_bias) ?? "unknown", hint: "funding headwind / tailwind" },
        { label: "basis_bias", value: asString(derivatives.basis_bias) ?? "unknown", hint: "basis direction" },
        { label: "taker_flow_alignment", value: asString(derivatives.taker_flow_alignment) ?? "unknown", hint: "taker flow alignment" },
        { label: "spread_bps", value: asNumber(derivatives.spread_bps) === null ? "unknown" : `${formatMaybeNumber(asNumber(derivatives.spread_bps), 2)}bps`, hint: "spread stress check" },
      ],
      alerts: [],
    },
    {
      key: "upcoming_event_risk",
      title: "Upcoming Event Risk",
      tone: activeRiskWindow ? "danger" : toneForSourceStatus(eventSourceStatus),
      items: [
        { label: "next_event_name", value: nextEventName, hint: "다음 예정 이벤트" },
        { label: "next_event_time", value: nextEventTime, hint: "UTC" },
        { label: "minutes_to_next_event", value: minutesToNextEvent === null ? "unknown" : `${minutesToNextEvent}분`, hint: "현재 시각 기준" },
        { label: "importance", value: nextEventImportance, hint: "event importance" },
        { label: "active_risk_window", value: activeRiskWindow ? "active" : "inactive", hint: asString(eventContext.summary_note) ?? "window summary" },
        { label: "source_status", value: eventSourceStatus, hint: `stale=${String(asBoolean(eventContext.is_stale) ?? false)} / complete=${String(asBoolean(eventContext.is_complete) ?? false)}` },
      ],
      alerts:
        eventSourceStatus !== "available" && eventSourceStatus !== "unknown"
          ? [{ tone: toneForSourceStatus(eventSourceStatus), text: `source status ${eventSourceStatus}` }]
          : [],
    },
    {
      key: "ai_event_view",
      title: "AI Event View",
      tone: aiSourceState === "available" ? "neutral" : "warn",
      items: [
        { label: "ai_bias", value: aiBias, hint: "AI event-aware bias" },
        { label: "ai_risk_state", value: aiRiskState, hint: "AI event-aware risk state" },
        { label: "ai_confidence", value: aiConfidence === null ? "unknown" : aiConfidence.toFixed(2), hint: "preview only" },
        { label: "source_state", value: aiSourceState, hint: "unknown / unavailable is explicit" },
        { label: "scenario_note", value: aiScenarioNote, hint: "AI scenario note" },
        { label: "confidence_penalty_reason", value: aiPenaltyReason, hint: "confidence penalty reason" },
      ],
      alerts:
        aiSourceState === "available"
          ? []
          : [{ tone: "warn", text: "AI event-aware output unavailable, showing explicit unknown values." }],
    },
    {
      key: "operator_event_view",
      title: "Operator Event View",
      tone: operatorEventView ? "neutral" : "warn",
      items: [
        { label: "operator_bias", value: describeEventBias(operatorEventView?.operator_bias), hint: "operator override bias" },
        { label: "operator_risk_state", value: describeRiskState(operatorEventView?.operator_risk_state), hint: "operator override risk state" },
        { label: "applies_to_symbols", value: operatorEventView && operatorEventView.applies_to_symbols.length > 0 ? operatorEventView.applies_to_symbols.join(", ") : "global", hint: "empty means global" },
        { label: "horizon", value: operatorEventView?.horizon ?? "unknown", hint: "operator horizon" },
        { label: "valid_window", value: `${formatUtcTimestamp(operatorEventView?.valid_from)} ~ ${formatUtcTimestamp(operatorEventView?.valid_to)}`, hint: "UTC" },
        { label: "enforcement_mode", value: describeEnforcementMode(operatorEventView?.enforcement_mode), hint: operatorEventView?.note ?? "operator note unavailable" },
      ],
      alerts:
        operatorEventView
          ? []
          : [{ tone: "warn", text: "Operator event view not configured. Unknown values are expected." }],
    },
    {
      key: "alignment_result",
      title: "Alignment Result",
      tone: toneForAlignment(alignmentDecision?.alignment_status),
      items: [
        { label: "alignment_status", value: describeAlignmentStatus(alignmentDecision?.alignment_status), hint: "enum-based alignment" },
        { label: "reason_codes", value: alignmentDecision && alignmentDecision.reason_codes.length > 0 ? alignmentDecision.reason_codes.join(", ") : "none", hint: "stable reason codes" },
        { label: "evaluated_at", value: formatUtcTimestamp(alignmentDecision?.evaluated_at), hint: "UTC" },
        { label: "ai/operator", value: `${aiBias} / ${describeEventBias(alignmentDecision?.operator_bias)}`, hint: `${aiRiskState} / ${describeRiskState(alignmentDecision?.operator_risk_state)}` },
      ],
      alerts: [],
    },
    {
      key: "effective_trading_policy_preview",
      title: "Effective Trading Policy Preview",
      tone: toneForPolicyPreview(effectivePolicyPreview),
      items: [
        { label: "effective_policy_preview", value: effectivePolicyPreview, hint: "shared event-policy evaluator output" },
        { label: "enforcement", value: "Entry path mirrored in risk_guard", hint: "reduce / exit / protective recovery remain exempt" },
      ],
      alerts: [{ tone: toneForPolicyPreview(effectivePolicyPreview), text: "Preview mirrors current risk_guard semantics for new entries." }],
    },
    {
      key: "manual_no_trade_window",
      title: "Manual No-Trade Window",
      tone: activeWindows.length > 0 ? "danger" : "neutral",
      items: [
        { label: "active_windows", value: String(activeWindows.length), hint: "any-active => preview force_no_trade_window" },
        { label: "latest_window_scope", value: manualWindows[0] ? describeWindowScope(manualWindows[0].scope) : "none", hint: manualWindows[0] ? manualWindows[0].window_id : "stored window 없음" },
        { label: "latest_window_time", value: manualWindows[0] ? `${formatUtcTimestamp(manualWindows[0].start_at)} ~ ${formatUtcTimestamp(manualWindows[0].end_at)}` : "none", hint: "UTC" },
        { label: "flags", value: manualWindows[0] ? `auto_resume=${String(manualWindows[0].auto_resume)} / require_manual_rearm=${String(manualWindows[0].require_manual_rearm)}` : "none", hint: manualWindows[0]?.reason ?? "window reason unavailable" },
      ],
      alerts:
        activeWindows.length > 0
          ? activeWindows.map((window) => ({
              tone: "danger" as const,
              text: `${window.window_id}: ${window.reason} (${formatUtcTimestamp(window.start_at)} ~ ${formatUtcTimestamp(window.end_at)})`,
            }))
          : [{ tone: "neutral", text: "활성 manual no-trade window 없음" }],
    },
    {
      key: "risk_guard_decision",
      title: "Risk Guard Decision",
      tone: symbol.risk_guard.allowed === false ? "danger" : symbol.risk_guard.allowed ? "good" : "neutral",
      items: [
        { label: "allowed", value: symbol.risk_guard.allowed === null ? "unknown" : symbol.risk_guard.allowed ? "allow" : "block", hint: "risk_guard final gate" },
        { label: "decision", value: symbol.risk_guard.decision ?? "unknown", hint: symbol.risk_guard.operating_state ?? "operating state unavailable" },
        { label: "approved_risk_pct", value: formatPercent(symbol.risk_guard.approved_risk_pct), hint: "approved risk percentage" },
        { label: "approved_leverage", value: symbol.risk_guard.approved_leverage === null ? "unknown" : `${formatMaybeNumber(symbol.risk_guard.approved_leverage, 1)}x`, hint: "approved leverage" },
        { label: "blocked_reason", value: riskBlockedReason, hint: "event-policy hard block code when present" },
        { label: "approval_required_reason", value: riskApprovalRequiredReason, hint: "manual approval code when present" },
        { label: "policy_source", value: riskPolicySource, hint: "source layer that produced the current event policy result" },
        { label: "execution_state", value: formatExecutionState(symbol.execution, symbol.risk_guard.allowed), hint: "latest execution status" },
      ],
      alerts: [],
    },
    {
      key: "blocked_degraded_reason",
      title: "Blocked / Degraded",
      tone: blockedReasons.length > 0 ? "danger" : degradedFlags.length > 0 ? "warn" : "neutral",
      items: [
        { label: "blocked_reason_count", value: String(blockedReasons.length), hint: blockedReasons.length > 0 ? blockedReasons.join(", ") : "blocked reason 없음" },
        { label: "degraded_flag_count", value: String(degradedFlags.length), hint: degradedFlags.length > 0 ? degradedFlags.map(translateFlag).join(", ") : "degraded flag 없음" },
      ],
      alerts: blockedAndDegradedAlerts,
    },
  ];
}
