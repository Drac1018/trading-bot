import { describeRiskReasonCode } from "./risk-reason-copy.js";

export type OperatorEventBias = "bullish" | "bearish" | "neutral" | "no_trade" | "unknown";
export type OperatorEventRiskState = "risk_on" | "risk_off" | "neutral" | "unknown";
export type OperatorEventAlignmentStatus = "aligned" | "partially_aligned" | "conflict" | "insufficient_data";
export type OperatorEventEnforcementMode =
  | "observe_only"
  | "approval_required"
  | "block_on_conflict"
  | "force_no_trade";
export type OperatorEventSourceStatus =
  | "available"
  | "fixture"
  | "stub"
  | "external_api"
  | "stale"
  | "incomplete"
  | "unavailable"
  | "error";
export type OperatorEventImportance = "low" | "medium" | "high" | "critical" | "unknown";
export type OperatorEffectivePolicyPreview =
  | "allow_normal"
  | "allow_with_approval"
  | "block_new_entries"
  | "force_no_trade_window"
  | "insufficient_data";
export type OperatorPolicySource =
  | "manual_no_trade_window"
  | "operator_enforcement_mode"
  | "operator_bias"
  | "alignment_policy"
  | "none";
export type AIEventSourceState = OperatorEventSourceStatus | "unknown";
export type EventControlTone = "good" | "warn" | "danger" | "neutral";
export type EventSourceDisplayKind = "event_context" | "ai_event_view";
export type EventSourceProvenance = "external_api" | "fixture" | "stub" | "external" | "disconnected" | "unknown";
export type EventSourceVendor = "fred" | "bls" | "bea";

export type OperatorEventItemPayload = {
  event_name: string;
  event_at: string;
  importance: OperatorEventImportance;
  affected_assets: string[];
  source_status?: OperatorEventSourceStatus;
  summary_note?: string | null;
};

export type OperatorActiveRiskWindowPayload = {
  is_active: boolean;
  event_name?: string | null;
  event_importance: OperatorEventImportance;
  start_at?: string | null;
  end_at?: string | null;
  affected_assets: string[];
  summary_note?: string | null;
};

export type OperatorEventContextPayload = {
  source_status: OperatorEventSourceStatus;
  source_provenance?: EventSourceProvenance | null;
  source_vendor?: EventSourceVendor | null;
  generated_at: string;
  is_stale: boolean;
  is_complete: boolean;
  active_risk_window: boolean;
  active_risk_window_detail?: OperatorActiveRiskWindowPayload | null;
  next_event_at?: string | null;
  next_event_name?: string | null;
  next_event_importance: OperatorEventImportance;
  minutes_to_next_event?: number | null;
  upcoming_events: OperatorEventItemPayload[];
  affected_assets: string[];
  enrichment_vendors?: EventSourceVendor[];
  summary_note?: string | null;
};

export type AIEventViewPayload = {
  ai_bias: OperatorEventBias;
  ai_risk_state: OperatorEventRiskState;
  ai_confidence?: number | null;
  scenario_note?: string | null;
  confidence_penalty_reason?: string | null;
  source_state: AIEventSourceState;
};

export type OperatorEventViewPayload = {
  operator_bias: OperatorEventBias;
  operator_risk_state: OperatorEventRiskState;
  applies_to_symbols: string[];
  horizon?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
  enforcement_mode: OperatorEventEnforcementMode;
  note?: string | null;
  created_by: string;
  updated_at?: string | null;
};

export type ManualNoTradeWindowScopePayload = {
  scope_type: "global" | "symbols";
  symbols: string[];
};

export type ManualNoTradeWindowPayload = {
  window_id: string;
  scope: ManualNoTradeWindowScopePayload;
  start_at: string;
  end_at: string;
  reason: string;
  auto_resume: boolean;
  require_manual_rearm: boolean;
  created_by: string;
  updated_at?: string | null;
  is_active: boolean;
};

export type AlignmentDecisionPayload = {
  ai_bias: OperatorEventBias;
  operator_bias: OperatorEventBias;
  ai_risk_state: OperatorEventRiskState;
  operator_risk_state: OperatorEventRiskState;
  alignment_status: OperatorEventAlignmentStatus;
  reason_codes: string[];
  effective_policy_preview: OperatorEffectivePolicyPreview;
  evaluated_at: string;
};

export type EvaluatedOperatorPolicyPayload = {
  operator_view_active: boolean;
  matched_window_id?: string | null;
  alignment_status: OperatorEventAlignmentStatus;
  enforcement_mode: OperatorEventEnforcementMode;
  reason_codes: string[];
  effective_policy_preview: OperatorEffectivePolicyPreview;
  event_source_status: OperatorEventSourceStatus;
  event_source_stale: boolean;
  evaluated_at: string;
};

export type EventOperatorControlPayload = {
  event_context: OperatorEventContextPayload;
  ai_event_view: AIEventViewPayload;
  operator_event_view: OperatorEventViewPayload;
  alignment_decision: AlignmentDecisionPayload;
  evaluated_operator_policy?: EvaluatedOperatorPolicyPayload | null;
  blocked_reason?: string | null;
  degraded_reason?: string | null;
  approval_required_reason?: string | null;
  policy_source?: OperatorPolicySource;
  manual_no_trade_windows: ManualNoTradeWindowPayload[];
  effective_policy_preview: OperatorEffectivePolicyPreview;
};

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function parseTimestamp(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function uniqueSymbols(values: string[]) {
  return Array.from(new Set(values.map((item) => item.trim().toUpperCase()).filter(Boolean)));
}

export function symbolsToCsv(values: string[]) {
  return uniqueSymbols(values).join(", ");
}

export function csvToSymbols(value: string) {
  return uniqueSymbols(value.split(","));
}

export function isoToUtcInputValue(value: string | null | undefined) {
  const parsed = parseTimestamp(value);
  if (!parsed) {
    return "";
  }
  return `${parsed.getUTCFullYear()}-${pad(parsed.getUTCMonth() + 1)}-${pad(parsed.getUTCDate())}T${pad(
    parsed.getUTCHours(),
  )}:${pad(parsed.getUTCMinutes())}`;
}

export function utcInputValueToIso(value: string) {
  const normalized = value.trim();
  if (!normalized) {
    return null;
  }
  const withSeconds = normalized.length === 16 ? `${normalized}:00` : normalized;
  return `${withSeconds}Z`;
}

export function formatUtcTimestamp(value: string | null | undefined) {
  const parsed = parseTimestamp(value);
  if (!parsed) {
    return "정보 없음";
  }
  return `${parsed.getUTCFullYear()}-${pad(parsed.getUTCMonth() + 1)}-${pad(parsed.getUTCDate())} ${pad(
    parsed.getUTCHours(),
  )}:${pad(parsed.getUTCMinutes())} UTC`;
}

export function describeEventBias(value: string | null | undefined) {
  const normalizedValue = String(value ?? "").trim().toLowerCase();
  switch (normalizedValue) {
    case "bullish":
      return "상승 쪽";
    case "bearish":
      return "하락 쪽";
    case "neutral":
      return "중립";
    case "no_trade":
      return "신규 진입 안 함";
    default:
      return "미설정";
  }
}

export function describeRiskState(value: string | null | undefined) {
  switch (value) {
    case "risk_on":
      return "위험 감수 가능";
    case "risk_off":
      return "위험 회피";
    case "neutral":
      return "중립";
    default:
      return "미설정";
  }
}

export function describeAlignmentStatus(value: string | null | undefined) {
  switch (value) {
    case "aligned":
      return "대체로 일치";
    case "partially_aligned":
      return "일부 차이";
    case "conflict":
      return "의견 충돌";
    default:
      return "판단 보류";
  }
}

export function describeEnforcementMode(value: string | null | undefined) {
  switch (value) {
    case "observe_only":
      return "참고만";
    case "approval_required":
      return "다르면 한 번 더 확인";
    case "block_on_conflict":
      return "충돌하면 신규 진입 중단";
    case "force_no_trade":
      return "신규 진입 중단";
    default:
      return "참고만";
  }
}

export function inferEventSourceProvenance(
  value:
    | Pick<OperatorEventContextPayload, "source_status" | "source_provenance">
    | Pick<OperatorEventItemPayload, "source_status">
    | { source_status?: string | null; source_provenance?: string | null }
    | null
    | undefined,
): EventSourceProvenance {
  const sourceProvenance =
    value && "source_provenance" in value ? String(value.source_provenance ?? "").trim().toLowerCase() : "";
  if (sourceProvenance === "external_api" || sourceProvenance === "fixture" || sourceProvenance === "stub") {
    return sourceProvenance;
  }
  const sourceStatus = String(value?.source_status ?? "").trim().toLowerCase();
  if (sourceStatus === "fixture") {
    return "fixture";
  }
  if (sourceStatus === "stub") {
    return "stub";
  }
  if (sourceStatus === "external_api") {
    return "external_api";
  }
  return "unknown";
}

export function normalizeSourceStatus(value: string | null | undefined) {
  switch (String(value ?? "").trim().toLowerCase()) {
    case "available":
    case "fixture":
    case "stub":
    case "external_api":
      return "available";
    case "stale":
    case "incomplete":
    case "unavailable":
    case "error":
      return String(value ?? "").trim().toLowerCase() as "stale" | "incomplete" | "unavailable" | "error";
    default:
      return "unknown";
  }
}

export function describeEventSourceProvenance(value: EventSourceProvenance | null | undefined) {
  switch (value) {
    case "external_api":
    case "external":
      return "external_api";
    case "fixture":
      return "fixture";
    case "stub":
      return "stub";
    case "disconnected":
      return "연결 안 됨";
    default:
      return "확인 중";
  }
}

export function describeEventSourceVendor(value: EventSourceVendor | null | undefined) {
  switch (value) {
    case "fred":
      return "FRED";
    case "bls":
      return "BLS";
    case "bea":
      return "BEA";
    default:
      return "확인 중";
  }
}

export function describeEnrichmentVendors(values: EventSourceVendor[] | null | undefined) {
  const normalized = Array.from(new Set((values ?? []).filter(Boolean)));
  return normalized.length > 0 ? normalized.map((value) => describeEventSourceVendor(value)).join(", ") : "없음";
}

export function describeSourceStatus(
  value: string | null | undefined,
  options?: { kind?: EventSourceDisplayKind },
) {
  const kind = options?.kind ?? "event_context";
  const normalizedValue = normalizeSourceStatus(value);
  switch (normalizedValue) {
    case "available":
      return "정상";
    case "stale":
      return "조금 늦음";
    case "incomplete":
      return "일부 누락";
    case "unavailable":
      return kind === "ai_event_view" ? "의견 없음" : "연결 안 됨";
    case "error":
      return "확인 오류";
    default:
      return kind === "ai_event_view" ? "미설정" : "확인 중";
  }
}

export function describeImportance(value: string | null | undefined) {
  switch (value) {
    case "low":
      return "낮음";
    case "medium":
      return "보통";
    case "high":
      return "높음";
    case "critical":
      return "매우 높음";
    default:
      return "정보 없음";
  }
}

export function describeEffectivePolicyPreview(value: string | null | undefined) {
  switch (value) {
    case "allow_normal":
      return "평소처럼 가능";
    case "allow_with_approval":
      return "승인 후 가능";
    case "block_new_entries":
      return "신규 진입 보류";
    case "force_no_trade_window":
      return "신규 진입 금지";
    default:
      return "판단 보류";
  }
}

export function describeWindowScope(scope: ManualNoTradeWindowScopePayload | null | undefined) {
  if (!scope) {
    return "전체 심볼";
  }
  if (scope.scope_type === "symbols") {
    return scope.symbols.length > 0 ? `선택 심볼: ${scope.symbols.join(", ")}` : "선택 심볼";
  }
  return "전체 심볼";
}

export function describePolicySource(value: string | null | undefined) {
  switch (value) {
    case "manual_no_trade_window":
      return "직접 설정한 진입 중지 시간";
    case "operator_enforcement_mode":
      return "운영자 적용 방식";
    case "operator_bias":
      return "운영자 방향 설정";
    case "alignment_policy":
      return "AI와 운영자 의견 비교";
    default:
      return "추가 제한 없음";
  }
}

export function describeSourceStatusHelp(
  value: string | null | undefined,
  options?: { kind?: EventSourceDisplayKind; provenance?: EventSourceProvenance | null | undefined },
) {
  const kind = options?.kind ?? "event_context";
  const provenance = options?.provenance ?? null;
  const normalizedValue = normalizeSourceStatus(value);
  if (kind === "event_context" && provenance === "fixture") {
    return "현재는 샘플 이벤트 일정으로 보여주고 있습니다. 실제 외부 일정 연동은 아직 연결되지 않았습니다.";
  }
  if (kind === "event_context" && provenance === "stub") {
    return "현재는 임시 예시 데이터를 보여주고 있습니다. 실제 이벤트 데이터를 아직 연결하지 않은 상태입니다.";
  }
  switch (value) {
    case "available":
      return kind === "ai_event_view"
        ? "AI가 이벤트 관련 의견을 남겨 두었습니다."
        : "이벤트 정보를 정상적으로 읽어 왔습니다.";
    case "fixture":
      return "현재는 샘플 이벤트 일정으로 보여주고 있습니다. 실제 외부 일정 연동은 아직 연결되지 않았습니다.";
    case "stub":
      return "현재는 임시 예시 데이터를 보여주고 있습니다. 실제 이벤트 데이터를 아직 연결하지 않은 상태입니다.";
    case "stale":
      return kind === "ai_event_view"
        ? "AI 의견이 최신 상황보다 조금 늦을 수 있습니다."
        : "이벤트 정보가 최신 상황보다 조금 늦을 수 있습니다.";
    case "incomplete":
      return kind === "ai_event_view"
        ? "AI 의견에 일부 정보가 비어 있습니다."
        : "이벤트 정보 중 일부가 비어 있습니다.";
    case "unavailable":
      return kind === "ai_event_view"
        ? "AI가 아직 이벤트 관련 의견을 남기지 않았습니다."
        : "이벤트 일정 데이터가 아직 연결되지 않았습니다.";
    case "error":
      return kind === "ai_event_view"
        ? "AI 이벤트 의견을 읽는 중 문제가 발생했습니다."
        : "이벤트 정보를 읽는 중 문제가 발생했습니다.";
    default:
      return kind === "ai_event_view"
        ? "AI 이벤트 의견 상태를 아직 확인하지 못했습니다."
        : "이벤트 정보 상태를 아직 확인하지 못했습니다.";
  }
}

export function describeEventReasonCode(value: string | null | undefined) {
  switch (value) {
    case "manual_no_trade_active":
      return "직접 설정한 신규 진입 중지 시간이 지금 적용 중입니다.";
    case "operator_no_trade":
      return "운영자 설정이 신규 진입 안 함으로 되어 있습니다.";
    case "operator_force_no_trade":
      return "운영자 설정상 지금은 신규 진입을 멈추도록 되어 있습니다.";
    case "operator_bias_no_trade":
      return "운영자 설정의 신규 진입 중지 시간이 현재 시각에 적용됩니다.";
    case "bias_conflict":
      return "AI 의견과 운영자 방향이 다릅니다.";
    case "risk_state_conflict":
      return "AI와 운영자가 보는 위험 수준이 다릅니다.";
    case "ai_unavailable":
      return "AI의 이벤트 의견이 아직 없습니다.";
    case "ai_stale":
      return "AI 의견이 최신 상황보다 조금 늦을 수 있습니다.";
    case "ai_incomplete":
      return "AI 의견에 일부 정보가 빠져 있습니다.";
    case "ai_error":
      return "AI 의견을 읽는 중 문제가 발생했습니다.";
    case "operator_unavailable":
      return "운영자 설정이 없거나 현재 심볼에는 적용되지 않습니다.";
    case "outside_valid_window":
      return "운영자 설정의 적용 시간이 아직 아니거나 이미 지났습니다.";
    case "approval_required_preview":
      return "현재 상태라면 신규 진입 전에 한 번 더 확인하는 것이 좋습니다.";
    case "block_on_conflict_preview":
      return "현재 상태라면 의견이 충돌할 때 신규 진입을 멈춥니다.";
    case "event_context_error":
      return "이벤트 정보를 읽는 중 문제가 발생했습니다.";
    case "event_context_stale":
      return "이벤트 정보가 최신 상황보다 조금 늦습니다.";
    case "event_context_incomplete":
      return "이벤트 정보가 일부 비어 있습니다.";
    case "event_context_unavailable":
      return "이벤트 정보를 아직 사용할 수 없습니다.";
    case "alignment_conflict_block":
      return "AI와 운영자 의견이 충돌해 신규 진입을 멈춥니다.";
    case "alignment_not_aligned":
      return "AI와 운영자 의견이 완전히 같지 않아 한 번 더 확인이 필요합니다.";
    case "alignment_insufficient_data":
      return "판단에 필요한 정보가 부족해 한 번 더 확인이 필요합니다.";
    default:
      return describeRiskReasonCode(value);
  }
}

export function describeManualWindowFlags(autoResume: boolean | null | undefined, requireManualRearm: boolean | null | undefined) {
  const autoResumeLabel = autoResume ? "예" : "아니오";
  const manualRearmLabel = requireManualRearm ? "예" : "아니오";
  return `종료 후 자동 복귀: ${autoResumeLabel} / 재개 전 수동 확인: ${manualRearmLabel}`;
}

export function summarizeReasonCodes(values: string[] | null | undefined) {
  if (!values || values.length === 0) {
    return "추가 사유 없음";
  }
  return values.map((value) => describeEventReasonCode(value)).join(" / ");
}

export function summarizeEntryPolicy(params: {
  effectivePolicyPreview?: string | null;
  blockedReason?: string | null;
  approvalRequiredReason?: string | null;
  policySource?: string | null;
}) {
  const policy = describeEffectivePolicyPreview(params.effectivePolicyPreview);
  const reason = describeEventReasonCode(params.blockedReason ?? params.approvalRequiredReason).replace(/[.。]+$/, "");
  const source = describePolicySource(params.policySource).replace(/[.。]+$/, "");
  return `지금 신규 진입은 "${policy}" 상태입니다. 이유: ${reason}. 기준: ${source}.`;
}

export function toneForSourceStatus(value: string | null | undefined): EventControlTone {
  switch (normalizeSourceStatus(value)) {
    case "available":
      return "good";
    case "unavailable":
      return "neutral";
    case "stale":
    case "incomplete":
      return "warn";
    case "error":
      return "danger";
    default:
      return "neutral";
  }
}

export function toneForAlignment(value: string | null | undefined): EventControlTone {
  switch (value) {
    case "aligned":
      return "good";
    case "partially_aligned":
      return "warn";
    case "conflict":
      return "danger";
    default:
      return "neutral";
  }
}

export function toneForPolicyPreview(value: string | null | undefined): EventControlTone {
  switch (value) {
    case "allow_normal":
      return "good";
    case "allow_with_approval":
      return "warn";
    case "block_new_entries":
    case "force_no_trade_window":
      return "danger";
    default:
      return "neutral";
  }
}
