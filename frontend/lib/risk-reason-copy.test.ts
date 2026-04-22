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
    "거래가 일시 중지되어 신규 진입을 차단했습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("ENTRY_TRIGGER_NOT_MET"),
    "현재 진입 트리거 조건이 충족되지 않았습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("PROTECTION_STATE_UNVERIFIED"),
    "보호주문 상태를 확인할 수 없어 신규 진입을 차단했습니다.",
  );
  assert.equal(
    lookupRiskReasonCode("LARGEST_POSITION_LIMIT_REACHED"),
    "심볼 집중도 한도 유지",
  );
  assert.equal(
    lookupRiskReasonCode("DETERMINISTIC_BASELINE_DISAGREEMENT"),
    "결정론적 기준선 불일치 상태 유지",
  );
  assert.equal(describeRiskReasonCode("UNKNOWN_CODE"), "UNKNOWN_CODE");
  assert.equal(describeRiskReasonCode(null), "추가 사유 없음");
});
