import assert from "node:assert/strict";
import test from "node:test";

type RiskReasonCopyModule = typeof import("./risk-reason-copy");

const riskReasonCopyModule = import(
  new URL("./risk-reason-copy.ts", import.meta.url).href,
) as Promise<RiskReasonCopyModule>;

test("lookupRiskReasonCode translates common risk_guard codes for operator-facing UI", async () => {
  const { lookupRiskReasonCode, describeRiskReasonCode } = await riskReasonCopyModule;

  assert.equal(
    lookupRiskReasonCode("TRADING_PAUSED"),
    "운영 중지 플래그가 켜져 있어 신규 주문을 보내지 않습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("ENTRY_TRIGGER_NOT_MET"),
    "가격이 진입 구간에 들어오거나 확인 캔들이 만들어져야 합니다.",
  );
  assert.equal(
    lookupRiskReasonCode("PROTECTION_STATE_UNVERIFIED"),
    "보호 주문이 검증되기 전에는 신규 진입을 잠시 보류합니다.",
  );
  assert.equal(
    lookupRiskReasonCode("LARGEST_POSITION_LIMIT_REACHED"),
    "요청 수량이 단일 심볼 한도를 초과했습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("DETERMINISTIC_BASELINE_DISAGREEMENT"),
    "AI는 진입을 제안했지만, 규칙 기반 기준선과 일치하지 않아 안전상 즉시 주문하지 않았습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("confidence_below_min_entry_threshold"),
    "AI가 감시용 진입 계획은 제안했지만 신뢰도가 최소 진입 기준보다 낮아 대기 진입 계획을 만들지 않았습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("PLAN_CANCELED_NO_ENTRY_CAPACITY"),
    "이미 열린 포지션 때문에 추가 진입 여유가 없어 대기 플랜 감시를 중단했습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("REPLACED_BY_NEW_APPROVED_PLAN"),
    "더 최신 승인 플랜으로 대체되어 이전 대기 플랜 감시를 중단했습니다.",
  );
  assert.equal(
    describeRiskReasonCode("UNKNOWN_CODE"),
    "아직 사용자용 설명 매핑이 없는 내부 코드입니다. 원본 코드: UNKNOWN_CODE",
  );
  assert.equal(describeRiskReasonCode(null), "추가 사유 없음");
});

test("confidence threshold block is shown as entry-wait reason", async () => {
  const { describeReasonCode } = await riskReasonCopyModule;

  const copy = describeReasonCode("confidence_below_min_entry_threshold");

  assert.equal(copy.known, true);
  assert.equal(copy.category, "entry_wait");
  assert.equal(copy.title_ko, "진입 신뢰도가 기준보다 낮아 대기 중입니다");
  assert.equal(copy.check_location_ko, "AI 의견 / 리스크 점검 > 예상 비용 점검");
});

test("decision hold rationale and AI budget skip codes are operator-facing", async () => {
  const { describeReasonCode, lookupRiskReasonCode } = await riskReasonCopyModule;

  const derivatives = describeReasonCode("DERIVATIVES_ALIGNMENT_HEADWIND");
  assert.equal(derivatives.known, true);
  assert.equal(derivatives.category, "entry_wait");
  assert.equal(derivatives.title_ko, "파생시장 정합성이 진입 방향을 뒷받침하지 않습니다");

  const breakout = describeReasonCode("BREAKOUT_OI_NOT_EXPANDING");
  assert.equal(breakout.known, true);
  assert.equal(breakout.category, "entry_wait");
  assert.equal(breakout.title_ko, "돌파 확인에 필요한 OI 증가가 없습니다");

  const budget = describeReasonCode("ROLE_DAILY_TOKEN_BUDGET_EXHAUSTED");
  assert.equal(budget.known, true);
  assert.equal(budget.category, "operational_control");
  assert.equal(lookupRiskReasonCode("ROLE_DAILY_TOKEN_BUDGET_EXHAUSTED"), budget.detail_ko);
});

test("position exit review local filter codes are operator-facing", async () => {
  const { describeReasonCode, lookupRiskReasonCode } = await riskReasonCopyModule;

  const partialNotReady = describeReasonCode("POSITION_EXIT_REVIEW_PARTIAL_NOT_READY");
  assert.equal(partialNotReady.known, true);
  assert.equal(partialNotReady.category, "safety_block");
  assert.equal(partialNotReady.title_ko, "부분익절 조건이 아직 충족되지 않았습니다");
  assert.match(partialNotReady.detail_ko, /부분익절 준비 상태/);

  const scalpRunnerBlocked = describeReasonCode("POSITION_EXIT_REVIEW_SCALP_RUNNER_BLOCKED");
  assert.equal(scalpRunnerBlocked.known, true);
  assert.equal(scalpRunnerBlocked.title_ko, "단타 포지션: 잔여 수량 축소 근거가 부족합니다");
  assert.match(scalpRunnerBlocked.operator_action_ko, /보유전략이 단타/);

  const swingSignal = describeReasonCode("POSITION_EXIT_REVIEW_SWING_RUNNER_SIGNAL_REQUIRED");
  assert.equal(swingSignal.known, true);
  assert.equal(swingSignal.title_ko, "스윙 포지션: 잔여 수량 훼손 근거가 아직 부족합니다");

  const positionTooEarly = describeReasonCode("POSITION_EXIT_REVIEW_POSITION_EXIT_TOO_EARLY");
  assert.equal(positionTooEarly.known, true);
  assert.equal(positionTooEarly.title_ko, "장기 보유 포지션: 전량 익절 근거가 아직 이릅니다");

  assert.equal(
    lookupRiskReasonCode("POSITION_EXIT_REVIEW_STOP_RELAXATION_IGNORED"),
    "AI 출력에 손절을 넓히거나 약화시키는 내용이 감지되어 적용하지 않고 감사 기록만 남겼습니다. 손절 권한은 규칙 기반 고정 손절에 남아 있습니다.",
  );
});

test("BTC long recent performance and exposure block is explained in operator Korean", async () => {
  const { describeReasonCode, describeReasonCodeInContext } = await riskReasonCopyModule;

  const symbolPerformance = describeReasonCode("SYMBOL_RECENT_PERFORMANCE_NEGATIVE");
  assert.equal(symbolPerformance.known, true);
  assert.equal(symbolPerformance.category, "safety_block");
  assert.equal(symbolPerformance.title_ko, "최근 BTCUSDT 실거래 성과가 수수료 차감 후 손실입니다");
  assert.match(symbolPerformance.detail_ko, /순손익/);

  const bucketPerformance = describeReasonCode("DECISION_BUCKET_RECENT_PERFORMANCE_NEGATIVE");
  assert.equal(bucketPerformance.title_ko, "BTCUSDT long 전환장 버킷의 최근 기대값이 음수입니다");
  assert.match(bucketPerformance.operator_action_ko, /기대값/);

  const exposure = describeReasonCode("CORRELATED_EXPOSURE_LIMIT_REACHED");
  assert.equal(exposure.title_ko, "BTC/ETH 같은 방향 노출 한도를 넘습니다");
  assert.match(exposure.operator_action_ko, /combined_BTC_ETH_directional_exposure_pct/);

  const combined = describeReasonCodeInContext("SYMBOL_RECENT_PERFORMANCE_NEGATIVE", [
    "SYMBOL_RECENT_PERFORMANCE_NEGATIVE",
    "DECISION_BUCKET_RECENT_PERFORMANCE_NEGATIVE",
    "CORRELATED_EXPOSURE_LIMIT_REACHED",
  ]);
  assert.equal(
    combined.title_ko,
    "BTCUSDT long 후보는 AI 승인 주문이 아니라 리스크 평가에서 차단된 관찰 후보입니다",
  );
  assert.match(combined.detail_ko, /AI 최종 판단은 HOLD/);
});
