import assert from "node:assert/strict";
import test from "node:test";

type CostBreakdownModule = typeof import("./cost-breakdown");

const costBreakdownModule = import(
  new URL("./cost-breakdown.ts", import.meta.url).href,
) as Promise<CostBreakdownModule>;

test("resolveCostBreakdownSelection defaults to current Seoul month and keeps supported periods", async () => {
  const { resolveCostBreakdownSelection } = await costBreakdownModule;
  const now = new Date("2026-05-06T16:00:00.000Z");

  assert.deepEqual(resolveCostBreakdownSelection({}, now), {
    period: "month",
    year: 2026,
    month: 5,
  });
  assert.deepEqual(resolveCostBreakdownSelection({ period: "year", year: "2025", month: "13" }, now), {
    period: "year",
    year: 2025,
    month: 5,
  });
});

test("formatters keep null ratios and incomplete slippage from looking like zero", async () => {
  const { formatCostBreakdownBps, formatCostBreakdownPercent, formatCostBreakdownUsdt } = await costBreakdownModule;

  assert.equal(formatCostBreakdownUsdt(12.3), "+12.30 USDT");
  assert.equal(formatCostBreakdownUsdt(-1.2), "-1.20 USDT");
  assert.equal(formatCostBreakdownPercent(null), "N/A");
  assert.equal(formatCostBreakdownBps(null, "COMPLETE"), "N/A");
  assert.equal(formatCostBreakdownBps(0, "INCOMPLETE"), "N/A");
  assert.equal(formatCostBreakdownBps(0.12, "COMPLETE"), "+0.12 bps");
});

test("metric labels hide raw internal keys and fall back safely", async () => {
  const { costMetricDescription, costMetricLabel, slippageWeightingLabel } = await costBreakdownModule;

  assert.equal(costMetricLabel("fee"), "수수료");
  assert.equal(costMetricLabel("maker_fee"), "Maker 수수료");
  assert.equal(costMetricLabel("funding_fee"), "펀딩비");
  assert.equal(costMetricLabel("realized_pnl"), "실현 손익");
  assert.equal(costMetricLabel("net_pnl"), "순손익");
  assert.equal(costMetricLabel("unknown_metric_key"), "알 수 없는 비용 항목");
  assert.equal(costMetricDescription("unknown_metric_key"), "아직 표시 라벨에 등록되지 않은 비용 항목입니다.");
  assert.equal(slippageWeightingLabel("quantity"), "수량 가중");
  assert.equal(slippageWeightingLabel("unexpected_weighting"), "알 수 없는 가중 방식");
});

test("quality badges expose missing close executions and incomplete sources", async () => {
  const { costBreakdownQualityBadges } = await costBreakdownModule;

  const badges = costBreakdownQualityBadges({
    realized_pnl_confirmed: false,
    execution_sync_status: "INCOMPLETE",
    funding_sync_status: "STALE",
    slippage_data_status: "UNKNOWN",
    missing_close_execution_count: 2,
    slippage_weighting: "quantity",
  });

  assert.deepEqual(
    badges.map((badge) => badge.label),
    ["실현 손익 미확정", "청산 체결 누락 2건", "체결 동기화 불완전", "펀딩 동기화 오래됨", "슬리피지 데이터 부족"],
  );
});

test("warnings translate API quality codes and include high fee ratio notice", async () => {
  const { costBreakdownWarningMessages } = await costBreakdownModule;

  const messages = costBreakdownWarningMessages({
    period: "month",
    timezone: "Asia/Seoul",
    start_at: "2026-05-01T00:00:00+09:00",
    end_at: "2026-06-01T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 4.49,
      gross_pnl_usdt: 5.33,
      fee_usdt: 0.86,
      funding_usdt: 0.02,
      total_cost_usdt: 0.86,
      fee_ratio_pct: 16.2,
      total_cost_ratio_pct: 16.2,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [],
    data_quality: {
      realized_pnl_confirmed: false,
      execution_sync_status: "INCOMPLETE",
      funding_sync_status: "STALE",
      slippage_data_status: "INCOMPLETE",
      missing_close_execution_count: 1,
    },
    warnings: [
      "missing_close_execution_count:1",
      "funding_sync_status:STALE",
      "slippage_data_status:INCOMPLETE",
    ],
  });

  assert.deepEqual(messages, [
    "수수료가 총손익의 +16.2%를 차지합니다.",
    "청산 체결 누락으로 실현 손익이 확정되지 않았습니다.",
    "펀딩비 동기화가 불완전합니다.",
    "슬리피지 데이터가 부족해 평균 체결 불리도를 확정할 수 없습니다.",
  ]);
});

test("bucket status marks ratio N/A when gross pnl is non-positive", async () => {
  const { costBreakdownBucketStatus } = await costBreakdownModule;

  const status = costBreakdownBucketStatus(
    {
      label: "2026-05-01",
      start_at: "2026-05-01T00:00:00+09:00",
      end_at: "2026-05-02T00:00:00+09:00",
      net_pnl_usdt: -1,
      gross_pnl_usdt: -1,
      fee_usdt: 0.2,
      funding_usdt: 0,
      total_cost_usdt: 0.2,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: 0.1,
      adverse_slippage_bps: 0.1,
    },
    {
      realized_pnl_confirmed: true,
      execution_sync_status: "COMPLETE",
      funding_sync_status: "COMPLETE",
      slippage_data_status: "COMPLETE",
      missing_close_execution_count: 0,
    },
  );

  assert.equal(status, "비율 N/A");
});
