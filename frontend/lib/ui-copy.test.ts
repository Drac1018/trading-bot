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
