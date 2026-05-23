export type ExecutionRiskProfileControl = {
  can_enter_new_position?: boolean | null;
  deterministic_market_profile?: string | null;
  ai_recommended_profile?: string | null;
  ai_recommendation_id?: string | null;
  ai_recommendation_confidence?: number | null;
  ai_recommendation_valid_until?: string | null;
  ai_recommendation_reason_codes?: string[] | null;
  ai_recommendation_status?: string | null;
  final_active_execution_profile?: string | null;
  shadow_final_execution_profile?: string | null;
  profile_selection_mode?: string | null;
  profile_selected_reason?: string | null;
  was_tightened_by_ai?: boolean | null;
  was_relaxation_blocked?: boolean | null;
  relaxation_block_reason?: string | null;
  relaxation_block_reason_codes?: string[] | null;
  ai_recommendation_ignored_reason_codes?: string[] | null;
  next_ai_settings_review_at?: string | null;
  ai_settings_shadow_mode?: boolean | null;
  ai_settings_auto_apply_mode?: string | null;
  profile_new_entry_blocked?: boolean | null;
  profile_survival_paths_allowed?: boolean | null;
};

export type ExecutionRiskProfileSummary = {
  deterministicProfile: string;
  aiRecommendedProfile: string;
  finalActiveProfile: string;
  shadowFinalProfile: string;
  recommendationId: string;
  confidenceLabel: string;
  recommendationStatus: string;
  recommendationStatusLabel: string;
  selectionMode: string;
  selectedReason: string;
  applicationStatus:
    | "shadow"
    | "applied_tighten"
    | "relaxation_blocked"
    | "ignored"
    | "not_applied";
  applicationLabel: string;
  applicationDetail: string;
  ignoredReasonCodes: string[];
  relaxationBlockReasonCodes: string[];
  finalActiveProfileLabel: string;
  profileBlockLabel: string;
  profileBlockDetail: string;
  newEntryLabel: string;
  newEntryDetail: string;
  survivalPathLabel: string;
  survivalPathDetail: string;
  nextReviewAt: string | null;
  validUntil: string | null;
  isProfileSelectorShadow: boolean;
};

function unique(values: string[]) {
  return [...new Set(values.filter((value) => value.trim().length > 0))];
}

function fallback(value: string | null | undefined) {
  return value && value.trim().length > 0 ? value : "-";
}

const profileLabels: Record<string, string> = {
  NORMAL: "정상",
  CAUTION: "주의",
  HIGH_VOLATILITY: "변동성 확대",
  THIN_LIQUIDITY: "유동성 얇음",
  STRESS: "위험 확대",
  DEGRADED: "보호 확인 필요",
};

const modeLabels: Record<string, string> = {
  off: "자동 적용 안 함",
  shadow: "관찰만",
  conservative_only: "보수화만 적용",
  manual_approval: "수동 승인",
};

const selectedReasonLabels: Record<string, string> = {
  ai_tightened_conservative_only: "AI 보수화 적용",
  ai_relaxation_blocked: "AI 완화 차단",
  deterministic_fallback: "기본 규칙 유지",
  no_ai_recommendation: "AI 추천 없음",
  shadow_no_auto_apply: "관찰 모드",
  profile_relaxation_blocked: "프로파일 완화 차단",
};

const reasonCodeLabels: Record<string, string> = {
  AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED: "추천 유효 시간 만료",
  AI_MARKET_SETTINGS_RECOMMENDATION_LOW_CONFIDENCE: "추천 신뢰도 부족",
  PROFILE_RELAXATION_CONSECUTIVE_CONFIRMATIONS: "완화 확인 횟수 부족",
  PROFILE_RELAXATION_DWELL_TIME: "프로파일 유지 시간 부족",
};

function profileLabel(value: string | null | undefined) {
  const raw = fallback(value);
  if (raw === "-") {
    return raw;
  }
  return profileLabels[raw.toUpperCase()] ?? raw;
}

function modeLabel(value: string | null | undefined) {
  const raw = fallback(value);
  if (raw === "-") {
    return raw;
  }
  return modeLabels[raw.toLowerCase()] ?? raw;
}

function selectedReasonLabel(value: string | null | undefined) {
  const raw = fallback(value);
  if (raw === "-") {
    return raw;
  }
  return selectedReasonLabels[raw] ?? raw;
}

function reasonCodeLabel(value: string) {
  return reasonCodeLabels[value] ?? value;
}

function confidenceLabel(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${Math.round(value * 100)}%`;
}

function statusLabel(status: string) {
  const normalized = status.trim().toLowerCase();
  if (normalized === "ignored") {
    return "무시됨";
  }
  if (normalized === "shadow_generated") {
    return "관찰 결과 생성";
  }
  if (normalized === "valid") {
    return "유효";
  }
  if (normalized === "expired") {
    return "만료";
  }
  return status || "확인 중";
}

export function buildExecutionRiskProfileSummary(
  control: ExecutionRiskProfileControl,
): ExecutionRiskProfileSummary {
  const selectionMode = fallback(control.profile_selection_mode);
  const autoApplyMode = fallback(control.ai_settings_auto_apply_mode);
  const isProfileSelectorShadow = selectionMode === "shadow" || autoApplyMode === "shadow";
  const ignoredReasonCodes = unique(control.ai_recommendation_ignored_reason_codes ?? []).map(reasonCodeLabel);
  const relaxationBlockReasonCodes = unique(control.relaxation_block_reason_codes ?? []).map(reasonCodeLabel);
  const status = fallback(control.ai_recommendation_status);
  const ignored = ignoredReasonCodes.length > 0 || status.toLowerCase() === "ignored";
  let applicationStatus: ExecutionRiskProfileSummary["applicationStatus"] = "not_applied";
  let applicationLabel = "기본 규칙 유지";
  let applicationDetail = selectedReasonLabel(control.profile_selected_reason);

  if (isProfileSelectorShadow) {
    applicationStatus = "shadow";
    applicationLabel = "관찰만 기록";
    applicationDetail = "AI 추천은 기록만 남기고 실제 실행 프로파일이나 신규 진입 차단을 바꾸지 않습니다.";
  } else if (control.was_tightened_by_ai) {
    applicationStatus = "applied_tighten";
    applicationLabel = "AI 보수화 반영";
    applicationDetail = "AI 추천이 기본 규칙보다 보수적이라 실행 프로파일을 더 엄격하게 조정했습니다.";
  } else if (control.was_relaxation_blocked) {
    applicationStatus = "relaxation_blocked";
    applicationLabel = "AI 완화 차단";
    applicationDetail =
      relaxationBlockReasonCodes.length > 0
        ? relaxationBlockReasonCodes.join(", ")
        : reasonCodeLabel(fallback(control.relaxation_block_reason));
  } else if (ignored) {
    applicationStatus = "ignored";
    applicationLabel = "AI 추천 무시";
    applicationDetail = ignoredReasonCodes.length > 0 ? ignoredReasonCodes.join(", ") : statusLabel(status);
  }

  const profileNewEntryBlocked = Boolean(control.profile_new_entry_blocked);
  const globalEntryAllowed = Boolean(control.can_enter_new_position);
  const profileBlockLabel = profileNewEntryBlocked
    ? "신규 진입 차단 적용"
    : isProfileSelectorShadow
      ? "관찰만"
      : "차단 미적용";
  const profileBlockDetail = profileNewEntryBlocked
    ? "현재 실행 프로파일이 신규 진입을 막고 있습니다."
    : "현재 실행 프로파일 자체는 신규 진입을 막지 않습니다. 신규 진입이 막혔다면 운영 상태, 동기화, 리스크 차단 사유를 함께 확인해야 합니다.";
  const finalActiveProfileLabel = `${profileLabel(control.final_active_execution_profile)} / ${profileBlockLabel}`;
  const newEntryLabel = profileNewEntryBlocked
    ? "신규 진입 차단"
    : globalEntryAllowed
      ? "신규 진입 가능"
      : "다른 보호 조건 확인 필요";
  const newEntryDetail = profileNewEntryBlocked
    ? "현재 실행 프로파일이 신규 진입을 막고 있습니다."
    : globalEntryAllowed
      ? "운영 상태 기준으로 신규 진입 조건이 열려 있습니다."
      : "실행 프로파일은 차단하지 않지만 다른 리스크, 실거래 승인, 동기화 조건 확인이 필요합니다.";

  const survivalAllowed = control.profile_survival_paths_allowed !== false;
  return {
    deterministicProfile: profileLabel(control.deterministic_market_profile),
    aiRecommendedProfile: profileLabel(control.ai_recommended_profile),
    finalActiveProfile: profileLabel(control.final_active_execution_profile),
    shadowFinalProfile: profileLabel(control.shadow_final_execution_profile),
    recommendationId: fallback(control.ai_recommendation_id),
    confidenceLabel: confidenceLabel(control.ai_recommendation_confidence),
    recommendationStatus: status,
    recommendationStatusLabel: statusLabel(status),
    selectionMode: modeLabel(selectionMode),
    selectedReason: selectedReasonLabel(control.profile_selected_reason),
    applicationStatus,
    applicationLabel,
    applicationDetail,
    ignoredReasonCodes,
    relaxationBlockReasonCodes,
    finalActiveProfileLabel,
    profileBlockLabel,
    profileBlockDetail,
    newEntryLabel,
    newEntryDetail,
    survivalPathLabel: survivalAllowed ? "허용" : "확인 필요",
    survivalPathDetail: survivalAllowed
      ? "축소, 청산, 비상 청산 경로는 신규 진입 차단과 별도로 유지됩니다."
      : "백엔드가 생존 경로 허용 상태를 확인하지 못했습니다.",
    nextReviewAt: control.next_ai_settings_review_at ?? null,
    validUntil: control.ai_recommendation_valid_until ?? null,
    isProfileSelectorShadow,
  };
}
