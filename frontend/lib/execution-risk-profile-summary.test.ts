import assert from "node:assert/strict";
import test from "node:test";

type ExecutionRiskProfileSummaryModule = typeof import("./execution-risk-profile-summary");

const executionRiskProfileSummaryModule = import(
  new URL("./execution-risk-profile-summary.ts", import.meta.url).href,
) as Promise<ExecutionRiskProfileSummaryModule>;

const baseControl = {
  can_enter_new_position: true,
  deterministic_market_profile: "NORMAL",
  ai_recommended_profile: "CAUTION",
  ai_recommendation_id: "rec-1",
  ai_recommendation_confidence: 0.81,
  ai_recommendation_valid_until: "2026-05-13T12:15:00",
  ai_recommendation_reason_codes: ["VOLATILITY_ELEVATED"],
  ai_recommendation_status: "shadow_generated",
  final_active_execution_profile: "CAUTION",
  shadow_final_execution_profile: null,
  profile_selection_mode: "conservative_only",
  profile_selected_reason: "ai_tightened_conservative_only",
  was_tightened_by_ai: true,
  was_relaxation_blocked: false,
  relaxation_block_reason: null,
  relaxation_block_reason_codes: [],
  ai_recommendation_ignored_reason_codes: [],
  next_ai_settings_review_at: "2026-05-13T12:15:00",
  ai_settings_shadow_mode: false,
  ai_settings_auto_apply_mode: "conservative_only",
  profile_new_entry_blocked: false,
  profile_survival_paths_allowed: true,
};

test("valid conservative AI recommendation is shown as applied tighten", async () => {
  const { buildExecutionRiskProfileSummary } = await executionRiskProfileSummaryModule;

  const summary = buildExecutionRiskProfileSummary(baseControl);

  assert.equal(summary.applicationStatus, "applied_tighten");
  assert.equal(summary.deterministicProfile, "정상");
  assert.equal(summary.aiRecommendedProfile, "주의");
  assert.equal(summary.finalActiveProfile, "주의");
  assert.equal(summary.finalActiveProfileLabel, "주의 / 차단 미적용");
  assert.equal(summary.profileBlockLabel, "차단 미적용");
  assert.equal(summary.newEntryLabel, "신규 진입 가능");
  assert.equal(summary.survivalPathLabel, "허용");
});

test("expired and low confidence recommendations are shown as ignored", async () => {
  const { buildExecutionRiskProfileSummary } = await executionRiskProfileSummaryModule;

  const summary = buildExecutionRiskProfileSummary({
    ...baseControl,
    ai_recommendation_status: "ignored",
    was_tightened_by_ai: false,
    ai_recommendation_ignored_reason_codes: [
      "AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED",
      "AI_MARKET_SETTINGS_RECOMMENDATION_LOW_CONFIDENCE",
    ],
  });

  assert.equal(summary.applicationStatus, "ignored");
  assert.deepEqual(summary.ignoredReasonCodes, [
    "추천 유효 시간 만료",
    "추천 신뢰도 부족",
  ]);
});

test("AI relaxation is shown as blocked when deterministic profile is stricter", async () => {
  const { buildExecutionRiskProfileSummary } = await executionRiskProfileSummaryModule;

  const summary = buildExecutionRiskProfileSummary({
    ...baseControl,
    deterministic_market_profile: "HIGH_VOLATILITY",
    ai_recommended_profile: "NORMAL",
    final_active_execution_profile: "HIGH_VOLATILITY",
    profile_selected_reason: "ai_relaxation_blocked",
    was_tightened_by_ai: false,
    was_relaxation_blocked: true,
    relaxation_block_reason_codes: ["PROFILE_RELAXATION_CONSECUTIVE_CONFIRMATIONS"],
  });

  assert.equal(summary.applicationStatus, "relaxation_blocked");
  assert.deepEqual(summary.relaxationBlockReasonCodes, ["완화 확인 횟수 부족"]);
  assert.equal(summary.finalActiveProfile, "변동성 확대");
});

test("shadow mode does not look like an active block", async () => {
  const { buildExecutionRiskProfileSummary } = await executionRiskProfileSummaryModule;

  const summary = buildExecutionRiskProfileSummary({
    ...baseControl,
    profile_selection_mode: "shadow",
    ai_settings_auto_apply_mode: "shadow",
    was_tightened_by_ai: false,
    shadow_final_execution_profile: "STRESS",
    final_active_execution_profile: "NORMAL",
    profile_new_entry_blocked: false,
  });

  assert.equal(summary.applicationStatus, "shadow");
  assert.equal(summary.shadowFinalProfile, "위험 확대");
  assert.equal(summary.finalActiveProfile, "정상");
  assert.equal(summary.finalActiveProfileLabel, "정상 / 관찰만");
  assert.equal(summary.profileBlockLabel, "관찰만");
  assert.equal(summary.newEntryLabel, "신규 진입 가능");
});

test("backend profile block is displayed separately from survival paths", async () => {
  const { buildExecutionRiskProfileSummary } = await executionRiskProfileSummaryModule;

  const summary = buildExecutionRiskProfileSummary({
    ...baseControl,
    can_enter_new_position: false,
    final_active_execution_profile: "DEGRADED",
    profile_new_entry_blocked: true,
    profile_survival_paths_allowed: true,
  });

  assert.equal(summary.newEntryLabel, "신규 진입 차단");
  assert.equal(summary.finalActiveProfileLabel, "보호 확인 필요 / 신규 진입 차단 적용");
  assert.equal(summary.profileBlockLabel, "신규 진입 차단 적용");
  assert.equal(summary.survivalPathLabel, "허용");
});
