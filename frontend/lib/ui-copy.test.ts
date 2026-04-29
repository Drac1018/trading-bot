import assert from "node:assert/strict";
import test from "node:test";

type UiCopyModule = typeof import("./ui-copy");

const uiCopyModule = import(
  new URL("./ui-copy.ts", import.meta.url).href,
) as Promise<UiCopyModule>;

test("ui-copy translates overview dashboard labels into operator-friendly Korean", async () => {
  const { translateLabel } = await uiCopyModule;

  assert.equal(translateLabel("rollout_mode"), "실거래 적용 단계");
  assert.equal(translateLabel("approval_window_open"), "실거래 승인 창");
  assert.equal(translateLabel("blocked_reasons_current_cycle"), "이번 판단 주기 차단 사유");
  assert.equal(translateLabel("freshness_seconds"), "마지막 동기화 후 지난 시간");
  assert.equal(translateLabel("headroom"), "추가 진입 여유");
});

test("ui-copy formats macro event context for operator decisions", async () => {
  const { formatMacroEventContextDetail, formatMacroEventContextSummary } = await uiCopyModule;

  const context = {
    source_status: "external_api",
    source_vendor: "fred",
    next_event_name: "US CPI",
    next_event_importance: "high",
    minutes_to_next_event: 10,
    active_risk_window: true,
    affected_assets: ["USD", "CRYPTO"],
    enrichment_vendors: ["bls", "bea"],
    bls_actual_enriched: true,
    bea_actual_enriched: true,
    event_risk_active: true,
    event_risk_reason_codes: ["MACRO_EVENT_IMMINENT", "MACRO_EVENT_ENRICHMENT_AVAILABLE"],
  };

  assert.equal(formatMacroEventContextSummary(context), "\uC8FC\uC694 \uACBD\uC81C \uC774\uBCA4\uD2B8 10\uBD84 \uC804");
  assert.ok(formatMacroEventContextDetail(context).includes("BLS"));
  assert.ok(formatMacroEventContextDetail(context).includes("BEA"));
});

test("ui-copy does not present stale macro event bias as a trade signal", async () => {
  const { formatMacroEventContextDetail, formatMacroEventContextSummary } = await uiCopyModule;

  const context = {
    source_status: "stale",
    source_vendor: "fred",
    next_event_name: "US CPI",
    next_event_importance: "high",
    minutes_to_next_event: 10,
    active_risk_window: true,
    is_stale: true,
    is_complete: false,
    is_incomplete: true,
    event_bias_used: null,
    event_risk_reason_codes: ["MACRO_EVENT_CONTEXT_STALE", "MACRO_EVENT_CONTEXT_INCOMPLETE"],
  };

  const staleSummary = "\uC774\uBCA4\uD2B8 \uB370\uC774\uD130 \uC9C0\uC5F0/\uBD88\uC644\uC804";
  assert.equal(formatMacroEventContextSummary(context), staleSummary);
  assert.ok(formatMacroEventContextDetail(context).includes(staleSummary));
  assert.doesNotMatch(formatMacroEventContextDetail(context), /bearish|bullish/);
});

test("ui-copy formats boolean and enum values with user-facing wording", async () => {
  const { formatDisplayValue, translateLabel } = await uiCopyModule;

  assert.equal(translateLabel("available_balance"), "사용 가능 잔고");
  assert.equal(translateLabel("max_withdraw_amount"), "출금 가능 최대 금액");
  assert.equal(formatDisplayValue(false, "app_live_armed"), "해제됨");
  assert.equal(formatDisplayValue(true, "degraded"), "신규 진입 보류");
  assert.equal(formatDisplayValue("approval_control"), "승인/운영 제어");
  assert.equal(formatDisplayValue("hold"), "신규 진입 대기");
  assert.equal(formatDisplayValue("stale"), "조금 늦음");
  assert.equal(formatDisplayValue("limited_live"), "제한된 실거래");
});

test("ui-copy keeps reason codes meaning-first for operator-facing tables", async () => {
  const { formatDisplayValue } = await uiCopyModule;

  assert.equal(
    formatDisplayValue("TRADING_PAUSED"),
    "거래가 일시 중지되어 신규 진입을 차단했습니다.",
  );
  assert.equal(
    formatDisplayValue("LIVE_APPROVAL_REQUIRED"),
    "실거래 승인 창이 닫혀 있어 신규 진입 전에 수동 승인이 필요합니다.",
  );
});

test("ui-copy exposes active-position suppression fields with operator wording", async () => {
  const { formatDisplayValue, translateLabel } = await uiCopyModule;

  assert.equal(translateLabel("suppression_active"), "진입 제안 억제");
  assert.equal(translateLabel("suppression_reason_code"), "진입 제안 억제 사유");
  assert.equal(translateLabel("allow_same_side_add_on"), "same-side add-on 허용");
  assert.equal(translateLabel("allowed_add_on_side"), "허용 add-on 방향");
  assert.equal(formatDisplayValue(true, "suppression_active"), "활성");
  assert.equal(formatDisplayValue(false, "allow_same_side_add_on"), "불가");
  assert.equal(formatDisplayValue("LARGEST_POSITION_LIMIT_REACHED"), "심볼 집중도 한도 유지");
  assert.equal(
    formatDisplayValue("DETERMINISTIC_BASELINE_DISAGREEMENT"),
    "결정론적 기준선 불일치 상태 유지",
  );
});

test("ui-copy separates AI review reason from market signal summary", async () => {
  const { formatDisplayValue, translateLabel } = await uiCopyModule;

  assert.equal(translateLabel("ai_trigger_summary"), "당시 시장 신호 요약");
  assert.equal(translateLabel("market_signal_summary"), "당시 시장 신호 요약");
  assert.equal(translateLabel("macro_event_risk_summary"), "이벤트 리스크");
  assert.equal(translateLabel("ai_review_type"), "AI 호출 분류");
  assert.equal(translateLabel("ai_trigger_reason_codes"), "AI 호출 세부 코드");
  assert.equal(formatDisplayValue("entry_candidate_review"), "신규 진입 후보 검토");
  assert.equal(formatDisplayValue("ENTRY_CANDIDATE_SELECTED"), "신규 진입 후보 선정");
  assert.equal(formatDisplayValue("ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"), "거래량 부족으로 AI 검토 생략");
  assert.equal(formatDisplayValue("MACRO_EVENT_IMMINENT"), "주요 경제 이벤트 임박으로 신규 진입 보수화");
  assert.equal(formatDisplayValue("MACRO_EVENT_RISK_WINDOW_ACTIVE"), "거시 이벤트 리스크 구간");
});
