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
    "AI는 진입을 제안했지만, 결정론적 기준선과 일치하지 않아 안전상 즉시 주문하지 않았습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("confidence_below_min_entry_threshold"),
    "AI가 감시용 진입 계획은 제안했지만 confidence가 최소 진입 기준보다 낮아 pending plan을 만들지 않았습니다.",
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
  assert.equal(copy.check_location_ko, "AI 의견 / 리스크 점검 > expected cost gate");
});
