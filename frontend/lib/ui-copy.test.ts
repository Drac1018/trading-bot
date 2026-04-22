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

test("ui-copy formats boolean and enum values with user-facing wording", async () => {
  const { formatDisplayValue } = await uiCopyModule;

  assert.equal(formatDisplayValue(true, "exchange_can_trade"), "가능");
  assert.equal(formatDisplayValue(false, "app_live_armed"), "해제됨");
  assert.equal(formatDisplayValue(true, "degraded"), "신규 진입 보류");
  assert.equal(formatDisplayValue("approval_control"), "승인/운영 제어");
  assert.equal(formatDisplayValue("stale"), "조금 늦음");
  assert.equal(formatDisplayValue("limited_live"), "제한된 실거래");
});

test("ui-copy explains exchange_can_trade without raw canTrade wording", async () => {
  const { exchangeCanTradeAccountHint } = await uiCopyModule;

  assert.equal(
    exchangeCanTradeAccountHint,
    "거래소 계좌 응답 기준으로 새 주문이 명시적으로 차단됐는지 보여줍니다. canTrade 필드가 없는 선물 응답은 차단으로 간주하지 않으며, 앱 실주문 준비 상태는 별도로 확인해야 합니다.",
  );
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
