import assert from "node:assert/strict";
import test from "node:test";

type OrderSettlementModule = typeof import("./order-settlement");
type OrderSettlementInput = import("./order-settlement").OrderSettlementInput;

const orderSettlementModule = import(
  new URL("./order-settlement.ts", import.meta.url).href
) as Promise<OrderSettlementModule>;

function order(overrides: Partial<OrderSettlementInput>): OrderSettlementInput {
  return {
    closeExecutionSyncStatus: "UNKNOWN",
    missingCloseExecution: false,
    realizedPnlConfirmed: true,
    pnlSource: "LOCAL_EXECUTIONS",
    feeSource: "LOCAL_EXECUTIONS",
    feeConfirmed: true,
    warningMessage: null,
    feeWarningMessage: null,
    ...overrides,
  };
}

test("settlement display hides realized PnL when close execution is missing", () => {
  return orderSettlementModule.then(({ summarizeSettlementDisplay }) => {
    const state = summarizeSettlementDisplay([
    order({
      closeExecutionSyncStatus: "MISSING",
      missingCloseExecution: true,
      realizedPnlConfirmed: false,
      warningMessage: "청산 체결 누락: 거래소 손익 미반영",
      feeWarningMessage: "청산 수수료 미반영",
    }),
  ]);

    assert.equal(state.realizedPnlDisplayMode, "pending");
    assert.equal(state.realizedPnlLabel, "동기화 대기");
    assert.equal(state.badgeLabel, "청산 체결 누락");
    assert.equal(state.realizedPnlDetail, "청산 체결 누락: 거래소 손익 미반영");
    assert.match(state.feeDetail, /로컬 executions 기준/);
    assert.match(state.feeDetail, /청산 수수료 미반영/);
  });
});

test("settlement display shows confirmed realized PnL after close execution backfill", () => {
  return orderSettlementModule.then(({ summarizeSettlementDisplay }) => {
    const state = summarizeSettlementDisplay([
    order({
      closeExecutionSyncStatus: "COMPLETE",
      missingCloseExecution: false,
      realizedPnlConfirmed: true,
      pnlSource: "EXCHANGE",
    }),
  ]);

    assert.equal(state.realizedPnlDisplayMode, "value");
    assert.equal(state.realizedPnlConfirmed, true);
    assert.equal(state.badgeLabel, "정산 확인됨");
    assert.equal(state.realizedPnlDetail, "Binance 원문 realizedPnL 반영");
  });
});
