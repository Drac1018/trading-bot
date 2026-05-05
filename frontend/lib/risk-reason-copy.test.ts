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
    "시스템 가드 모드로 신규 진입을 보류했습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("ENTRY_TRIGGER_NOT_MET"),
    "현재 진입 트리거 조건이 충족되지 않았습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("PROTECTION_STATE_UNVERIFIED"),
    "보호주문 상태 확인이 끝나지 않아 신규 진입을 잠시 보류합니다.",
  );
  assert.equal(
    lookupRiskReasonCode("LARGEST_POSITION_LIMIT_REACHED"),
    "요청 수량이 단일 심볼 한도를 초과했습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("DETERMINISTIC_BASELINE_DISAGREEMENT"),
    "AI 최종 판단과 결정론적 기준선이 달라 즉시 주문을 보류했습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("PLAN_CANCELED_NO_ENTRY_CAPACITY"),
    "이미 열린 포지션 때문에 추가 진입 여유가 없어 대기 플랜 감시를 중단했습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("REPLACED_BY_NEW_APPROVED_PLAN"),
    "더 최신 승인 플랜으로 대체되어 이전 대기 플랜 감시를 중단했습니다.",
  );
  assert.equal(describeRiskReasonCode("UNKNOWN_CODE"), "UNKNOWN_CODE");
  assert.equal(describeRiskReasonCode(null), "추가 사유 없음");
});
