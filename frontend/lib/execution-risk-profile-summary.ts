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
    return "shadow 생성";
  }
  if (normalized === "valid") {
    return "유효";
  }
  if (normalized === "expired") {
    return "만료";
  }
  return status || "unknown";
}

export function buildExecutionRiskProfileSummary(
  control: ExecutionRiskProfileControl,
): ExecutionRiskProfileSummary {
  const selectionMode = fallback(control.profile_selection_mode);
  const autoApplyMode = fallback(control.ai_settings_auto_apply_mode);
  const isProfileSelectorShadow = selectionMode === "shadow" || autoApplyMode === "shadow";
  const ignoredReasonCodes = unique(control.ai_recommendation_ignored_reason_codes ?? []);
  const relaxationBlockReasonCodes = unique(control.relaxation_block_reason_codes ?? []);
  const status = fallback(control.ai_recommendation_status);
  const ignored = ignoredReasonCodes.length > 0 || status.toLowerCase() === "ignored";
  let applicationStatus: ExecutionRiskProfileSummary["applicationStatus"] = "not_applied";
  let applicationLabel = "deterministic 유지";
  let applicationDetail = fallback(control.profile_selected_reason);

  if (isProfileSelectorShadow) {
    applicationStatus = "shadow";
    applicationLabel = "shadow 기록만";
    applicationDetail = "AI 추천은 active profile이나 주문 차단으로 적용되지 않습니다.";
  } else if (control.was_tightened_by_ai) {
    applicationStatus = "applied_tighten";
    applicationLabel = "AI 보수화 반영";
    applicationDetail = "AI 추천이 deterministic profile보다 보수적이라 active profile을 조였습니다.";
  } else if (control.was_relaxation_blocked) {
    applicationStatus = "relaxation_blocked";
    applicationLabel = "AI 완화 차단";
    applicationDetail =
      relaxationBlockReasonCodes.length > 0
        ? relaxationBlockReasonCodes.join(", ")
        : fallback(control.relaxation_block_reason);
  } else if (ignored) {
    applicationStatus = "ignored";
    applicationLabel = "AI 추천 무시";
    applicationDetail = ignoredReasonCodes.length > 0 ? ignoredReasonCodes.join(", ") : status;
  }

  const profileNewEntryBlocked = Boolean(control.profile_new_entry_blocked);
  const globalEntryAllowed = Boolean(control.can_enter_new_position);
  const newEntryLabel = profileNewEntryBlocked
    ? "신규 진입 차단"
    : globalEntryAllowed
      ? "신규 진입 가능"
      : "프로파일 차단 없음";
  const newEntryDetail = profileNewEntryBlocked
    ? "final active profile gate가 신규 진입을 막고 있습니다."
    : globalEntryAllowed
      ? "현재 operator control 기준 신규 진입 gate가 열려 있습니다."
      : "profile gate는 차단하지 않지만 다른 risk/live/sync gate 확인이 필요합니다.";

  const survivalAllowed = control.profile_survival_paths_allowed !== false;
  return {
    deterministicProfile: fallback(control.deterministic_market_profile),
    aiRecommendedProfile: fallback(control.ai_recommended_profile),
    finalActiveProfile: fallback(control.final_active_execution_profile),
    shadowFinalProfile: fallback(control.shadow_final_execution_profile),
    recommendationId: fallback(control.ai_recommendation_id),
    confidenceLabel: confidenceLabel(control.ai_recommendation_confidence),
    recommendationStatus: status,
    recommendationStatusLabel: statusLabel(status),
    selectionMode,
    selectedReason: fallback(control.profile_selected_reason),
    applicationStatus,
    applicationLabel,
    applicationDetail,
    ignoredReasonCodes,
    relaxationBlockReasonCodes,
    newEntryLabel,
    newEntryDetail,
    survivalPathLabel: survivalAllowed ? "허용" : "확인 필요",
    survivalPathDetail: survivalAllowed
      ? "reduce, exit, emergency_exit 경로는 신규 진입 profile gate와 분리됩니다."
      : "백엔드가 생존 경로 허용 상태를 확인하지 못했습니다.",
    nextReviewAt: control.next_ai_settings_review_at ?? null,
    validUntil: control.ai_recommendation_valid_until ?? null,
    isProfileSelectorShadow,
  };
}
