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
  resolveOperatorEventViewConfigured,
  summarizeEntryPolicy,
  summarizeReasonCodes,
  toneForAlignment,
  toneForPolicyPreview,
  toneForSourceStatus,
  type EventOperatorControlPayload,
} from "./event-operator-control.js";

export type OperatorDetailTone = "good" | "warn" | "danger" | "neutral";

type MacroEventContextSummary = {
  source_status?: string | null;
  source_vendor?: string | null;
  next_event_name?: string | null;
  next_event_importance?: string | null;
  minutes_to_next_event?: number | null;
  active_risk_window?: boolean | null;
  release_reaction_window?: boolean | null;
  is_stale?: boolean | null;
  is_complete?: boolean | null;
  is_incomplete?: boolean | null;
  affected_assets?: string[] | null;
  enrichment_vendors?: string[] | null;
  failed_release_ids?: number[] | null;
  parse_failed_release_ids?: number[] | null;
  complete_reference?: Record<string, unknown> | null;
  bls_actual_enriched?: boolean | null;
  bea_actual_enriched?: boolean | null;
  event_risk_active?: boolean | null;
  event_risk_reason_codes?: string[] | null;
  event_bias_used?: string | null;
};

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
    | "ai_review_reason"
    | "market_signal_summary"
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
    ai_review?: {
      review_type?: string | null;
      trigger_reason?: string | null;
      trigger_reason_codes?: string[] | null;
      skip_reason?: string | null;
      dedupe_reason?: string | null;
      provider_status?: string | null;
      provider_invoked?: boolean | null;
      provider_skipped?: boolean | null;
      invoked_at?: string | null;
      provider_name?: string | null;
    } | null;
    ai_review_type?: string | null;
    last_ai_trigger_reason?: string | null;
    ai_trigger_reason_codes?: string[] | null;
    ai_skip_reason?: string | null;
    last_ai_skip_reason?: string | null;
    ai_trigger_summary?: string | null;
    market_signal_summary?: string | null;
    market_signal_context?: Record<string, unknown> | null;
    macro_event_context_summary?: MacroEventContextSummary | null;
    macro_event_risk_summary?: MacroEventContextSummary | null;
    event_risk_acknowledgement?: string | null;
    confidence_penalty_reason?: string | null;
    scenario_note?: string | null;
    psychology_scene_review?: Record<string, unknown> | null;
    psychology_scene_performance?: Record<string, unknown> | null;
  };
  risk_guard: {
    allowed: boolean | null;
    decision: string | null;
    operating_state: string | null;
    approved_risk_pct: number | null;
    approved_leverage: number | null;
    blocked_reason_codes: string[];
    degraded_reason_codes?: string[];
    protection_reason_codes?: string[];
    blocked_reason?: string | null;
    degraded_reason?: string | null;
    approval_required_reason?: string | null;
    survival_path?: string | null;
    policy_source?: string | null;
  };
  execution: {
    order_id: number | null;
    execution_status: string | null;
    order_status: string | null;
  };
  open_position?: {
    is_open?: boolean | null;
    position_exit_review?: Record<string, unknown> | null;
  } | null;
  blocked_reasons: string[];
  stale_flags: string[];
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function asNumberArray(value: unknown): number[] {
  return Array.isArray(value)
    ? value.filter((item): item is number => typeof item === "number" && Number.isFinite(item))
    : [];
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

function uniqueNumbers(values: number[]) {
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

const displayValueMap: Record<string, string> = {
  entry_candidate_review: "신규 진입 후보 검토",
  entry_candidate_event: "신규 진입 후보 검토",
  breakout_exception_review: "돌파 예외 검토",
  breakout_exception_event: "돌파 예외 검토",
  protection_review: "보호 상태 점검",
  protection_review_event: "보호 상태 점검",
  manual_review: "수동 검토",
  manual_review_event: "수동 검토",
  skipped_pre_ai: "AI 검토 생략",
  invoked: "AI 검토 실행",
  deduped: "중복 생략",
  ENTRY_CANDIDATE_SELECTED: "신규 진입 후보 선정",
  ENTRY_CANDIDATE_WEAK_VOLUME_PREAI: "거래량 부족으로 AI 검토 생략",
  ENTRY_CANDIDATE_NEUTRAL_CONTEXT_HOLD_BACKOFF: "반복 중립 후보라 AI 검토 생략",
  ENTRY_CANDIDATE_NEUTRAL_CONTEXT_PREAI: "중립 신호라 AI 검토 생략",
  ENTRY_CANDIDATE_LOW_ACTIONABILITY_HOLD_BACKOFF: "반복 저효용 후보라 AI 검토 생략",
  ENTRY_CANDIDATE_ORDER_PATH_NOT_ACTIONABLE: "주문 경로 미준비로 AI 검토 생략",
  ENTRY_CANDIDATE_ACTIVE_PENDING_PLAN_PREAI: "기존 대기 진입안으로 AI 검토 생략",
  ENTRY_CANDIDATE_INCOMPLETE_TRADE_PLAN_PREAI: "진입 구조 불완전으로 AI 검토 생략",
  ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN: "최근 같은 장면의 AI hold 판단 재사용",
  AI_ENTRY_OUTPUT_INCOMPLETE: "AI 진입안 구조 불완전으로 hold 정규화",
  ROLE_DAILY_TOKEN_BUDGET_EXHAUSTED: "일일 AI 토큰 예산 소진",
  SOFT_SIGNAL_AI_REVIEW: "약한 후보 AI 검토 대상",
  SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE: "약한 관망 후보라 AI 호출 전 억제",
  SOFT_SIGNAL_REVIEW_COOLDOWN_ACTIVE: "약한 후보 전환감시 쿨다운",
  SOFT_SIGNAL_REVIEW_NO_MATERIAL_CHANGE: "약한 후보 변화 부족으로 AI 생략",
  AI_CYCLE_BUDGET_EXHAUSTED: "사이클 AI 예산 초과로 신규 후보 검토 생략",
  SOFT_SIGNAL_TRANSITION_WATCH: "약한 후보 전환감시",
  SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_WATCH: "직접 진입 대신 대기 계획",
  SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_HOLD: "약한 후보 직접 진입 차단",
  DERIVATIVES_ALIGNMENT_HEADWIND: "파생시장 정합성 부족",
  BREAKOUT_OI_SPREAD_FILTER: "돌파 OI/스프레드 조건 부족",
  BREAKOUT_OI_NOT_EXPANDING: "돌파 OI 확장 없음",
  MACRO_EVENT_IMMINENT: "주요 경제 이벤트 임박으로 신규 진입 보수화",
  MACRO_EVENT_RISK_WINDOW_ACTIVE: "거시 이벤트 리스크 구간",
  MACRO_RELEASE_REACTION_WINDOW: "발표 직후 변동성 구간",
  MACRO_EVENT_CONTEXT_STALE: "이벤트 데이터 지연",
  MACRO_EVENT_CONTEXT_INCOMPLETE: "이벤트 데이터 불완전",
  MACRO_EVENT_ENRICHMENT_AVAILABLE: "경제지표 실제값 반영 가능",
  HOLD_DECISION: "현재는 신규 진입 신호가 없어 대기 중입니다.",
  STALE_MARKET_DATA: "시장 데이터 지연",
  low: "낮음",
  medium: "보통",
  high: "높음",
  fred: "FRED",
  bls: "BLS",
  bea: "BEA",
  external_api: "external_api",
  available: "정상",
  stale: "조금 늦음",
  incomplete: "일부 누락",
  unavailable: "연결 안 됨",
};

function formatDisplayValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "boolean") {
    return value ? "예" : "아니오";
  }
  if (typeof value === "number") {
    return value.toLocaleString("ko-KR");
  }
  if (typeof value === "string") {
    return displayValueMap[value] ?? value;
  }
  return String(value);
}

function hasMacroEventContext(value: Record<string, unknown>) {
  return Boolean(
    value.source_status ||
      value.next_event_name ||
      value.event_risk_active ||
      asStringArray(value.event_risk_reason_codes).length > 0,
  );
}

function macroEventDataUncertain(value: Record<string, unknown>) {
  return Boolean(
    asBoolean(value.is_stale) ||
      asBoolean(value.is_incomplete) ||
      asBoolean(value.is_complete) === false ||
      ["stale", "incomplete", "unavailable", "error"].includes(String(value.source_status ?? "")),
  );
}

function macroEventMinuteText(minutes: number) {
  if (minutes < 0) {
    return `${Math.abs(minutes)}분 전 발표`;
  }
  return `${minutes}분 전`;
}

function formatMacroEventContextSummary(value: Record<string, unknown>) {
  if (!hasMacroEventContext(value)) {
    return "이벤트 정보 없음";
  }
  if (macroEventDataUncertain(value)) {
    return "이벤트 데이터 지연/불완전";
  }
  if (asBoolean(value.release_reaction_window)) {
    return "발표 직후 변동성 구간";
  }
  const eventName = asString(value.next_event_name);
  const isHighImpact = asString(value.next_event_importance) === "high";
  const minutes = asNumber(value.minutes_to_next_event);
  if (minutes !== null && minutes < 0 && asBoolean(value.active_risk_window)) {
    return "발표 직후 변동성 구간";
  }
  if (minutes !== null) {
    const prefix = isHighImpact ? "주요 경제 이벤트" : eventName ?? "경제 이벤트";
    return `${prefix} ${macroEventMinuteText(minutes)}`;
  }
  if (asBoolean(value.active_risk_window)) {
    return "거시 이벤트 리스크 구간";
  }
  return eventName ?? "이벤트 정보 있음";
}

function formatMacroEventContextDetail(value: Record<string, unknown>) {
  if (!hasMacroEventContext(value)) {
    return "AI 판단 당시 이벤트 컨텍스트 없음";
  }
  const parts: string[] = [];
  if (macroEventDataUncertain(value)) {
    parts.push("이벤트 데이터 지연/불완전");
  }
  const eventName = asString(value.next_event_name);
  if (eventName) {
    parts.push(eventName);
  }
  const eventImportance = asString(value.next_event_importance);
  if (eventImportance) {
    parts.push(`중요도 ${formatDisplayValue(eventImportance)}`);
  }
  if (asBoolean(value.active_risk_window)) {
    parts.push("이벤트 리스크 구간 활성");
  }
  if (asBoolean(value.release_reaction_window)) {
    parts.push("발표 직후 변동성 구간");
  }
  if (asBoolean(value.bls_actual_enriched)) {
    parts.push("BLS 실제값 반영됨");
  }
  if (asBoolean(value.bea_actual_enriched)) {
    parts.push("BEA 실제값 반영됨");
  }
  const sourceStatus = asString(value.source_status);
  if (sourceStatus) {
    const vendor = asString(value.source_vendor) ? ` / ${formatDisplayValue(asString(value.source_vendor))}` : "";
    parts.push(`source ${formatDisplayValue(sourceStatus)}${vendor}`);
  }
  const affectedAssets = asStringArray(value.affected_assets);
  if (affectedAssets.length > 0) {
    parts.push(`자산 ${affectedAssets.join(", ")}`);
  }
  const reasonCodes = asStringArray(value.event_risk_reason_codes);
  if (reasonCodes.length > 0) {
    parts.push(`사유 ${reasonCodes.map((code) => formatDisplayValue(code)).join(", ")}`);
  }
  return parts.join(" / ");
}

function hasRecordValues(value: Record<string, unknown>) {
  return Object.keys(value).length > 0;
}

function formatOptionalDisplay(value: unknown) {
  const text = asString(value);
  return text ? formatDisplayValue(text) : "정보 없음";
}

function formatAiReviewClassification(value: string | null) {
  if (!value) {
    return "정보 없음";
  }
  const labels: Record<string, string> = {
    entry_candidate_review: "신규 진입 후보 검토",
    entry_candidate_event: "신규 진입 후보 검토",
    breakout_exception_review: "돌파 예외 검토",
    breakout_exception_event: "돌파 예외 검토",
    protection_review: "보호 상태 점검",
    protection_review_event: "보호 상태 점검",
    manual_review: "수동 검토",
    manual_review_event: "수동 검토",
    skipped_pre_ai: "AI 검토 생략",
  };
  return labels[value] ?? formatDisplayValue(value);
}

function formatReasonCodes(values: string[]) {
  return values.length > 0 ? values.map((code) => formatDisplayValue(code)).join(" / ") : "없음";
}

function describeRiskReason(value: string | null | undefined) {
  if (!value) {
    return "없음";
  }
  const display = formatDisplayValue(value);
  return display === value ? describeEventReasonCode(value) : display;
}

function formatProviderStatus(review: Record<string, unknown>, skipReason: string | null) {
  const providerStatus = asString(review.provider_status);
  if (asBoolean(review.provider_invoked) === true || providerStatus === "invoked") {
    return "AI 검토 실행";
  }
  if (asBoolean(review.trigger_deduped) === true || providerStatus === "deduped" || skipReason === "TRIGGER_DEDUPED") {
    return "중복 생략";
  }
  if (asBoolean(review.provider_skipped) === true || skipReason || providerStatus === "skipped_pre_ai") {
    return "AI 검토 생략";
  }
  return providerStatus ? formatDisplayValue(providerStatus) : "정보 없음";
}

export function buildOperatorDetailSections(symbol: OperatorDetailSymbolLike): OperatorDetailSection[] {
  const regime = asRecord(symbol.market_context_summary);
  const derivatives = asRecord(symbol.derivatives_summary);
  const legacyEventContext = asRecord(symbol.event_context_summary);
  const aiReview = asRecord(symbol.ai_decision.ai_review);
  const marketSignalContext = asRecord(symbol.ai_decision.market_signal_context);
  const macroEventRiskContext = asRecord(
    symbol.ai_decision.macro_event_risk_summary ?? symbol.ai_decision.macro_event_context_summary,
  );
  const eventControl = symbol.event_operator_control ?? null;
  const currentEventContext = asRecord(eventControl?.event_context ?? legacyEventContext);
  const eventContext = currentEventContext;
  const aiMacroEventContext = macroEventRiskContext;
  const operatorEventView = eventControl?.operator_event_view ?? null;
  const operatorEventViewConfigured = resolveOperatorEventViewConfigured(eventControl);
  const alignmentDecision = eventControl?.alignment_decision ?? null;
  const manualWindows = eventControl?.manual_no_trade_windows ?? [];
  const activeWindows = manualWindows.filter((window) => window.is_active);

  const blockedReasons = unique(
    symbol.risk_guard.blocked_reason_codes.length > 0
      ? symbol.risk_guard.blocked_reason_codes
      : symbol.blocked_reasons,
  );
  const degradedFlags = unique(symbol.stale_flags);
  const degradedReasons = unique([
    ...(symbol.risk_guard.degraded_reason_codes ?? []),
    ...(symbol.risk_guard.degraded_reason ? [symbol.risk_guard.degraded_reason] : []),
  ]);
  const protectionReasons = unique(symbol.risk_guard.protection_reason_codes ?? []);

  const aiReviewType = asString(aiReview.review_type) ?? symbol.ai_decision.ai_review_type ?? null;
  const aiTriggerReason = asString(aiReview.trigger_reason) ?? symbol.ai_decision.last_ai_trigger_reason ?? null;
  const aiTriggerReasonCodes = unique(
    asStringArray(aiReview.trigger_reason_codes).length > 0
      ? asStringArray(aiReview.trigger_reason_codes)
      : asStringArray(symbol.ai_decision.ai_trigger_reason_codes),
  );
  const aiSkipReason =
    asString(aiReview.skip_reason) ?? symbol.ai_decision.ai_skip_reason ?? symbol.ai_decision.last_ai_skip_reason ?? null;
  const marketSignalSummary =
    asString(symbol.ai_decision.market_signal_summary) ??
    asString(marketSignalContext.summary) ??
    asString(symbol.ai_decision.ai_trigger_summary) ??
    "정보 없음";

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
      : null;
  const aiScenarioNote =
    eventControl?.ai_event_view?.scenario_note
    ?? symbol.ai_decision.event_risk_acknowledgement
    ?? "정보 없음";
  const aiPenaltyReason =
    eventControl?.ai_event_view?.confidence_penalty_reason
    ?? "정보 없음";

  const nextEventName = asString(eventContext.next_event_name) ?? "정보 없음";
  const minutesToNextEvent = asNumber(eventContext.minutes_to_next_event);
  const activeRiskWindow = asBoolean(eventContext.active_risk_window) ?? false;
  const affectedAssets = asStringArray(eventContext.affected_assets);
  const completeReference = asRecord(eventContext.complete_reference);
  const missingReleaseIds = uniqueNumbers([
    ...asNumberArray(eventContext.failed_release_ids),
    ...asNumberArray(eventContext.parse_failed_release_ids),
  ]);
  const hasCompleteReference = rawEventSourceStatus === "incomplete" && hasRecordValues(completeReference);
  const completeReferenceEventName = asString(completeReference.next_event_name) ?? "정보 없음";
  const completeReferenceGeneratedAt = asString(completeReference.generated_at);

  const riskBlockedReason = symbol.risk_guard.blocked_reason ?? null;
  const riskApprovalRequiredReason = symbol.risk_guard.approval_required_reason ?? null;
  const riskDegradedReason = symbol.risk_guard.degraded_reason ?? null;
  const riskPolicySource = symbol.risk_guard.policy_source ?? "none";

  const riskBlockedReasonText =
    riskBlockedReason !== null ? describeRiskReason(riskBlockedReason) : formatReasonCodes(blockedReasons);

  const policySummary = summarizeEntryPolicy({
    effectivePolicyPreview: rawEffectivePolicyPreview,
    blockedReason: riskBlockedReason ?? eventControl?.blocked_reason,
    approvalRequiredReason: riskApprovalRequiredReason ?? eventControl?.approval_required_reason,
    policySource: riskPolicySource,
    operatorViewConfigured: operatorEventViewConfigured,
  });

  const blockedAndDegradedAlerts: OperatorDetailAlert[] = [
    ...blockedReasons.map((code) => ({
      tone: code === riskApprovalRequiredReason ? "warn" as const : "danger" as const,
      text: describeRiskReason(code),
    })),
    ...degradedReasons.map((code) => ({ tone: "warn" as const, text: describeRiskReason(code) })),
    ...protectionReasons.map((code) => ({ tone: "warn" as const, text: describeRiskReason(code) })),
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
      text: describeRiskReason(riskDegradedReason),
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
      key: "ai_review_reason",
      title: "AI 검토 사유",
      tone: aiSkipReason ? "warn" : "neutral",
      items: [
        {
          label: "검토 분류",
          value: formatAiReviewClassification(aiReviewType ?? aiTriggerReason),
          hint: "신규 진입 후보 검토, 돌파 예외 검토, 보호 상태 점검처럼 실제 검토 경로를 보여줍니다.",
        },
        {
          label: "AI 호출 사유",
          value: aiTriggerReason ?? "정보 없음",
          hint: "AI 호출 원인이 되는 런타임 트리거입니다. 시장 지표 요약과 분리해서 봅니다.",
        },
        {
          label: "AI 호출 사유 코드",
          value: formatReasonCodes(aiTriggerReasonCodes),
          hint: "AI 호출 전 차단/트리거가 남긴 내부 사유 코드입니다.",
        },
        {
          label: "AI 검토 생략",
          value: aiSkipReason ? formatDisplayValue(aiSkipReason) : "없음",
          hint: aiSkipReason ? "판단 전 생략 여부" : "AI 호출 전 생략이 없으면 없음으로 표시합니다.",
        },
        {
          label: "공급자 상태",
          value: formatProviderStatus(aiReview, aiSkipReason),
          hint: asString(aiReview.provider_name) ?? "provider 정보 없음",
        },
      ],
      alerts: aiSkipReason ? [{ tone: "warn", text: formatDisplayValue(aiSkipReason) }] : [],
    },
    {
      key: "market_signal_summary",
      title: "당시 시장 신호 요약",
      tone: marketSignalSummary === "정보 없음" ? "warn" : "neutral",
      items: [
        {
          label: "요약",
          value: marketSignalSummary,
          hint: "AI 호출 원인이 아니라 판단 당시 시장 지표 요약입니다.",
        },
        {
          label: "모멘텀",
          value: formatOptionalDisplay(marketSignalContext.momentum_state ?? regime.momentum_state),
          hint: "AI 판단 당시 값이 있으면 우선 사용하고, 과거 payload는 현재 레짐 요약으로 보완합니다.",
        },
        {
          label: "거래량",
          value: formatOptionalDisplay(marketSignalContext.volume_regime ?? regime.volume_regime),
          hint: "거래량 강도 또는 AI 호출 전 약한 거래량 판단을 이해하기 위한 시장 지표입니다.",
        },
        {
          label: "추세 정렬",
          value: formatOptionalDisplay(marketSignalContext.trend_alignment ?? regime.trend_alignment),
          hint: "상위 흐름과 같은 쪽인지 보여줍니다.",
        },
        {
          label: "데이터 품질",
          value: formatReasonCodes(asStringArray(marketSignalContext.data_quality_flags)),
          hint: "stale/incomplete 같은 시장 입력 상태가 있으면 표시합니다.",
        },
      ],
      alerts: [],
    },
    {
      key: "upcoming_event_risk",
      title: "거시 이벤트 리스크",
      tone: activeRiskWindow ? "danger" : toneForSourceStatus(rawEventSourceStatus),
      items: [
        {
          label: "이벤트 요약",
          value: formatMacroEventContextSummary(eventContext),
          hint: formatMacroEventContextDetail(eventContext),
        },
        {
          label: "AI 판단 당시 이벤트",
          value: hasMacroEventContext(aiMacroEventContext)
            ? formatMacroEventContextSummary(aiMacroEventContext)
            : "정보 없음",
          hint: formatMacroEventContextDetail(aiMacroEventContext),
        },
        {
          label: "직전 완전본",
          value: hasCompleteReference
            ? `${completeReferenceEventName} / ${formatUtcTimestamp(completeReferenceGeneratedAt)}`
            : "없음",
          hint: hasCompleteReference
            ? "최신 조회 일부 실패 / 직전 완전본 참고"
            : "최신 이벤트 조회가 완전하면 별도 참고본이 필요 없습니다.",
        },
        {
          label: "누락 release",
          value: missingReleaseIds.length > 0 ? missingReleaseIds.join(", ") : "없음",
          hint: "FRED release fetch 또는 parse 실패가 있으면 release_id를 보여줍니다.",
        },
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
          label: "BLS 실제값",
          value: boolToKorean(asBoolean(eventContext.bls_actual_enriched)),
          hint: "BLS 실제값 enrichment가 반영됐는지 보여줍니다.",
        },
        {
          label: "BEA 실제값",
          value: boolToKorean(asBoolean(eventContext.bea_actual_enriched)),
          hint: "BEA 실제값 enrichment가 반영됐는지 보여줍니다.",
        },
        {
          label: "영향 자산",
          value: affectedAssets.length > 0 ? affectedAssets.join(", ") : "정보 없음",
          hint: "이벤트 컨텍스트가 영향 대상으로 표시한 자산입니다.",
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
        [
          ...(normalizedEventSourceStatus !== "available" && normalizedEventSourceStatus !== "unknown"
            ? [{
                tone: toneForSourceStatus(rawEventSourceStatus),
                text: describeSourceStatusHelp(rawEventSourceStatus, {
                  kind: "event_context",
                  provenance: eventSourceProvenance,
                }),
              }]
            : []),
          ...(hasCompleteReference
            ? [{
                tone: "warn" as const,
                text: "최신 조회 일부 실패 / 직전 완전본 참고",
              }]
            : []),
        ],
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
      tone: operatorEventViewConfigured ? "neutral" : "warn",
      items: [
        { label: "운영자 방향", value: describeEventBias(operatorEventView?.operator_bias), hint: "운영자가 직접 정한 대응 방향입니다." },
        { label: "운영자 위험 판단", value: describeRiskState(operatorEventView?.operator_risk_state), hint: "운영자가 본 현재 위험 수준입니다." },
        {
          label: "적용 심볼",
          value:
            operatorEventViewConfigured && operatorEventView && operatorEventView.applies_to_symbols.length > 0
              ? operatorEventView.applies_to_symbols.join(", ")
              : "저장된 override 없음",
          hint: "비워 두면 모든 심볼에 적용됩니다.",
        },
        {
          label: "영향 기간/관점",
          value: operatorEventViewConfigured ? (operatorEventView?.horizon ?? "정보 없음") : "저장된 override 없음",
          hint: "예: 오늘 이벤트 전후, 이번 주 등으로 적습니다.",
        },
        {
          label: "적용 시간",
          value: operatorEventViewConfigured
            ? `${formatUtcTimestamp(operatorEventView?.valid_from)} ~ ${formatUtcTimestamp(operatorEventView?.valid_to)}`
            : "저장된 시간 범위 없음",
          hint: "모든 시각은 UTC 기준입니다.",
        },
        {
          label: "반영 방식",
          value: operatorEventViewConfigured
            ? describeEnforcementMode(operatorEventView?.enforcement_mode)
            : "저장된 override 없음 (기본 참고 평가만 사용)",
          hint: operatorEventViewConfigured ? (operatorEventView?.note ?? "저장된 메모 없음") : "운영자 이벤트 설정이 아직 없습니다.",
        },
      ],
      alerts:
        operatorEventViewConfigured
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
          value: riskBlockedReasonText,
          hint: "신규 진입이 막힌 가장 직접적인 이유입니다.",
        },
        {
          label: "추가 확인 사유",
          value: describeRiskReason(riskApprovalRequiredReason),
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
      tone: blockedReasons.length > 0
        ? "danger"
        : degradedReasons.length > 0 || protectionReasons.length > 0 || degradedFlags.length > 0
          ? "warn"
          : "neutral",
      items: [
        {
          label: "현재 차단 이유",
          value: formatReasonCodes(blockedReasons),
          hint: "지금 신규 진입을 막고 있는 이유입니다.",
        },
        {
          label: "주의가 필요한 상태",
          value:
            degradedReasons.length > 0
              ? formatReasonCodes(degradedReasons)
              : degradedFlags.length > 0
                ? degradedFlags.map((flag) => translateFlag(flag)).join(" / ")
                : "없음",
          hint: "운영 상태 저하, 데이터 지연, 불완전 상태를 함께 보여줍니다.",
        },
        {
          label: "보호/미해결 주문 사유",
          value:
            protectionReasons.length > 0
              ? formatReasonCodes(protectionReasons)
              : "없음",
          hint: "보호 복구와 미해결 제출 가드처럼 신규 진입과 별도인 안전 경로입니다.",
        },
      ],
      alerts: blockedAndDegradedAlerts,
    },
  ];
}
