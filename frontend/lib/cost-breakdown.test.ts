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
  const {
    formatCostBreakdownBps,
    formatCostBreakdownPercent,
    formatCostBreakdownUsdt,
    formatProfitabilityCostSlippageBps,
  } = await costBreakdownModule;

  assert.equal(formatCostBreakdownUsdt(12.3), "+12.30 USDT");
  assert.equal(formatCostBreakdownUsdt(-1.2), "-1.20 USDT");
  assert.equal(formatCostBreakdownPercent(null), "N/A");
  assert.equal(formatCostBreakdownBps(null, "COMPLETE"), "N/A");
  assert.equal(formatCostBreakdownBps(0, "INCOMPLETE"), "N/A");
  assert.equal(formatCostBreakdownBps(0.12, "COMPLETE"), "+0.12 bps");
  assert.equal(formatCostBreakdownBps(0.12, " complete "), "+0.12 bps");
  assert.equal(
    formatProfitabilityCostSlippageBps(0.12, { slippage_data_status: " complete " }),
    "+0.12 bps",
  );
  assert.equal(
    formatProfitabilityCostSlippageBps(0.12, {
      slippage_data_status: null,
      slippage_data_reason: " no_execution_slippage_sample ",
    }),
    "-",
  );
  assert.equal(formatProfitabilityCostSlippageBps(null, { slippage_data_status: "COMPLETE" }), "-");
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
  const { costBreakdownBucketStatus, costBreakdownQualityBadges, costBreakdownStatusTone } =
    await costBreakdownModule;

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
    ["실현 손익 미확정", "청산 체결 누락 2건", "체결 동기화 불완전", "펀딩 동기화 오래됨", "슬리피지 상태 확인 필요"],
  );
  assert.deepEqual(
    badges.map((badge) => badge.tone),
    ["danger", "danger", "warn", "warn", "danger"],
  );

  assert.equal(costBreakdownStatusTone("COMPLETE"), "good");
  assert.equal(costBreakdownStatusTone("STALE"), "warn");
  assert.equal(costBreakdownStatusTone("INCOMPLETE"), "warn");
  assert.equal(costBreakdownStatusTone("UNKNOWN"), "danger");
  assert.equal(costBreakdownStatusTone(" not_ready "), "danger");
  assert.equal(costBreakdownStatusTone("BLOCKED"), "danger");
  assert.equal(costBreakdownStatusTone(""), "danger");
  assert.deepEqual(
    costBreakdownQualityBadges({
      realized_pnl_confirmed: true,
      execution_sync_status: "UNKNOWN",
      funding_sync_status: "UNKNOWN",
      slippage_data_status: "COMPLETE",
      slippage_sample_count: 1,
      missing_slippage_sample_count: 0,
      missing_close_execution_count: 0,
    }).map((badge) => badge.tone),
    ["good", "danger", "danger"],
  );

  assert.equal(
    costBreakdownBucketStatus(
      {
        label: "2026-05-26",
        start_at: "2026-05-26T00:00:00+09:00",
        end_at: "2026-05-27T00:00:00+09:00",
        net_pnl_usdt: 0,
        gross_pnl_usdt: 0,
        fee_usdt: 0,
        funding_usdt: 0,
        total_cost_usdt: 0,
        fee_ratio_pct: null,
        total_cost_ratio_pct: null,
        signed_slippage_bps: null,
        adverse_slippage_bps: null,
      },
      {
        realized_pnl_confirmed: true,
        execution_sync_status: "COMPLETE",
        funding_sync_status: "COMPLETE",
        slippage_data_status: "UNKNOWN",
        missing_close_execution_count: 0,
      },
    ),
    "슬리피지 상태 확인 필요",
  );
});

test("quality badges distinguish missing slippage samples from incomplete slippage data", async () => {
  const {
    costBreakdownBucketSlippageStatus,
    costBreakdownQualityBadges,
    costBreakdownWarningMessages,
    profitabilityCostWarningLabel,
    slippageDataQualityLabel,
    slippageDataQualityStatus,
    slippageDataQualityTone,
    statusLabel,
  } = await costBreakdownModule;

  const noSampleQuality = {
    realized_pnl_confirmed: true,
    execution_sync_status: "COMPLETE",
    funding_sync_status: "COMPLETE",
    slippage_data_status: "NO_SAMPLE",
    slippage_data_reason: "no_execution_slippage_sample",
    slippage_sample_count: 0,
    missing_slippage_sample_count: 0,
    missing_close_execution_count: 0,
    slippage_weighting: "quantity",
  };
  const badges = costBreakdownQualityBadges(noSampleQuality);
  const reasonOnlyNoSampleQuality = {
    ...noSampleQuality,
    slippage_data_status: "",
  };
  const staleUnknownNoSampleQuality = {
    ...noSampleQuality,
    slippage_data_status: "UNKNOWN",
  };

  assert.equal(statusLabel("NO_SAMPLE"), "표본 없음");
  assert.equal(statusLabel("NOT_READY"), "준비 안 됨");
  assert.equal(statusLabel("BLOCKED"), "차단됨");
  assert.equal(statusLabel(" complete "), "완료");
  assert.equal(slippageDataQualityLabel(noSampleQuality), "슬리피지 표본 없음: 체결 표본 없음");
  assert.equal(
    slippageDataQualityLabel({
      slippage_data_status: "INCOMPLETE",
      slippage_data_reason: "missing_execution_slippage_sample",
      missing_slippage_sample_count: 2,
    }),
    "슬리피지 데이터 부족: 일부 체결 슬리피지 누락 2건",
  );
  assert.equal(
    slippageDataQualityLabel({
      slippage_data_status: "UNKNOWN",
      slippage_data_reason: "slippage_status_unknown",
    }),
    "슬리피지 상태 확인 필요: 상태 원인 미확정",
  );
  assert.equal(
    slippageDataQualityLabel({
      slippage_data_status: "UNKNOWN",
      slippage_data_reason: null,
      slippage_sample_count: null,
      missing_slippage_sample_count: null,
    }),
    "슬리피지 상태 확인 필요",
  );
  assert.equal(
    slippageDataQualityLabel({
      slippage_data_status: "NOT_READY",
      slippage_data_reason: "slippage_not_ready",
    }),
    "슬리피지 준비 부족: 슬리피지 준비 상태 부족",
  );
  assert.equal(
    slippageDataQualityLabel({
      slippage_data_status: "BLOCKED",
      slippage_data_reason: "slippage_blocked",
    }),
    "슬리피지 게시 차단: 슬리피지 게시 차단",
  );
  assert.equal(slippageDataQualityTone("NO_SAMPLE"), "warn");
  assert.equal(slippageDataQualityTone(" unknown "), "danger");
  assert.equal(slippageDataQualityTone("not_ready"), "danger");
  assert.equal(slippageDataQualityTone("BLOCKED"), "danger");
  assert.equal(slippageDataQualityLabel(reasonOnlyNoSampleQuality), slippageDataQualityLabel(noSampleQuality));
  assert.equal(slippageDataQualityLabel(staleUnknownNoSampleQuality), slippageDataQualityLabel(noSampleQuality));
  assert.equal(slippageDataQualityStatus(staleUnknownNoSampleQuality), "NO_SAMPLE");
  assert.equal(slippageDataQualityTone(slippageDataQualityStatus(staleUnknownNoSampleQuality)), "warn");
  assert.deepEqual(
    costBreakdownQualityBadges(reasonOnlyNoSampleQuality).map((badge) => [badge.label, badge.tone]),
    badges.map((badge) => [badge.label, badge.tone]),
  );
  assert.deepEqual(
    costBreakdownQualityBadges(staleUnknownNoSampleQuality).map((badge) => [badge.label, badge.tone]),
    badges.map((badge) => [badge.label, badge.tone]),
  );
  assert.equal(
    costBreakdownBucketSlippageStatus({ slippage_data_status: null }, reasonOnlyNoSampleQuality),
    "NO_SAMPLE",
  );
  assert.deepEqual(
    badges.map((badge) => badge.label),
    ["실현 손익 확정", "슬리피지 표본 없음: 체결 표본 없음"],
  );
  assert.deepEqual(
    badges.map((badge) => badge.tone),
    ["good", "warn"],
  );
  assert.equal(
    costBreakdownQualityBadges({
      ...noSampleQuality,
      slippage_data_status: "NOT_READY",
      slippage_data_reason: "slippage_not_ready",
    }).at(-1)?.tone,
    "danger",
  );
  assert.equal(
    costBreakdownQualityBadges({
      ...noSampleQuality,
      slippage_data_status: "BLOCKED",
      slippage_data_reason: "slippage_blocked",
    }).at(-1)?.tone,
    "danger",
  );
  assert.deepEqual(
    costBreakdownQualityBadges({
      realized_pnl_confirmed: true,
      execution_sync_status: " complete ",
      funding_sync_status: "complete",
      slippage_data_status: "complete",
      slippage_sample_count: 2,
      missing_slippage_sample_count: 0,
      missing_close_execution_count: 0,
    }).map((badge) => badge.label),
    ["실현 손익 확정"],
  );

  const reasonOnlyNoSamplePayload = {
    period: "today" as const,
    timezone: "Asia/Seoul",
    start_at: "2026-05-25T00:00:00+09:00",
    end_at: "2026-05-26T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [],
    data_quality: reasonOnlyNoSampleQuality,
    warnings: [],
  };
  assert.deepEqual(
    costBreakdownWarningMessages(reasonOnlyNoSamplePayload),
    costBreakdownWarningMessages({
      ...reasonOnlyNoSamplePayload,
      data_quality: noSampleQuality,
      warnings: ["slippage_data_status:NO_SAMPLE"],
    }),
  );
  assert.deepEqual(
    costBreakdownWarningMessages({
      ...reasonOnlyNoSamplePayload,
      data_quality: staleUnknownNoSampleQuality,
      warnings: ["slippage_data_status:UNKNOWN"],
    }),
    costBreakdownWarningMessages({
      ...reasonOnlyNoSamplePayload,
      data_quality: noSampleQuality,
      warnings: ["slippage_data_status:NO_SAMPLE"],
    }),
  );
  assert.deepEqual(
    costBreakdownWarningMessages({
      period: "today",
      timezone: "Asia/Seoul",
      start_at: "2026-05-25T00:00:00+09:00",
      end_at: "2026-05-26T00:00:00+09:00",
      summary: {
        net_pnl_usdt: 0,
        gross_pnl_usdt: 0,
        fee_usdt: 0,
        funding_usdt: 0,
        total_cost_usdt: 0,
        fee_ratio_pct: null,
        total_cost_ratio_pct: null,
        signed_slippage_bps: null,
        adverse_slippage_bps: null,
      },
      buckets: [],
      data_quality: {
        realized_pnl_confirmed: true,
        execution_sync_status: "COMPLETE",
        funding_sync_status: "COMPLETE",
        slippage_data_status: "NO_SAMPLE",
        slippage_data_reason: "no_execution_slippage_sample",
        slippage_sample_count: 0,
        missing_slippage_sample_count: 0,
        missing_close_execution_count: 0,
      },
      warnings: ["slippage_data_status:NO_SAMPLE"],
    }),
    [profitabilityCostWarningLabel("slippage_data_status:NO_SAMPLE")],
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
      execution_sync_status: "COMPLETE",
      funding_sync_status: "STALE",
      slippage_data_status: "INCOMPLETE",
      missing_close_execution_count: 1,
    },
    warnings: [
      " missing_close_execution_count:1 ",
      "funding_sync_status:STALE",
      "slippage_data_status:INCOMPLETE",
    ],
  });

  assert.deepEqual(messages, [
    "수수료가 총손익의 +16.2%를 차지합니다.",
    "청산 체결 누락으로 실현 손익이 확정되지 않았습니다.",
    "펀딩비 동기화가 오래되어 비용 합계를 확정할 수 없습니다.",
    "일부 체결에 슬리피지 값이 없어 평균 체결 불리도가 불완전합니다.",
  ]);
});

test("warnings read data quality warning codes when response warnings are absent", async () => {
  const { costBreakdownWarningMessages } = await costBreakdownModule;

  const messages = costBreakdownWarningMessages({
    period: "today" as const,
    timezone: "Asia/Seoul",
    start_at: "2026-05-26T00:00:00+09:00",
    end_at: "2026-05-27T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [],
    data_quality: {
      realized_pnl_confirmed: false,
      execution_sync_status: "COMPLETE",
      funding_sync_status: "COMPLETE",
      slippage_data_status: "COMPLETE",
      missing_close_execution_count: 1,
      warning_codes: ["missing_close_execution_count:1"],
    },
    warnings: null,
  });

  assert.equal(messages.length, 1);
});

test("warnings synthesize missing close execution count from data quality", async () => {
  const { costBreakdownWarningMessages } = await costBreakdownModule;

  const payload = {
    period: "today" as const,
    timezone: "Asia/Seoul",
    start_at: "2026-05-26T00:00:00+09:00",
    end_at: "2026-05-27T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [],
    data_quality: {
      realized_pnl_confirmed: false,
      execution_sync_status: "COMPLETE",
      funding_sync_status: "COMPLETE",
      slippage_data_status: "COMPLETE",
      missing_close_execution_count: 2,
      warning_codes: null,
    },
    warnings: null,
  };

  assert.deepEqual(
    costBreakdownWarningMessages(payload),
    costBreakdownWarningMessages({ ...payload, warnings: ["missing_close_execution_count:2"] }),
  );
});

test("warnings read bucket warning codes when summary warnings are absent", async () => {
  const { costBreakdownWarningMessages } = await costBreakdownModule;

  const payload = {
    period: "today" as const,
    timezone: "Asia/Seoul",
    start_at: "2026-05-26T00:00:00+09:00",
    end_at: "2026-05-27T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [
      {
        label: "today",
        start_at: "2026-05-26T00:00:00+09:00",
        end_at: "2026-05-27T00:00:00+09:00",
        net_pnl_usdt: 0,
        gross_pnl_usdt: 0,
        fee_usdt: 0,
        funding_usdt: 0,
        total_cost_usdt: 0,
        fee_ratio_pct: null,
        total_cost_ratio_pct: null,
        signed_slippage_bps: null,
        adverse_slippage_bps: null,
        slippage_data_status: "NO_SAMPLE",
        warning_codes: ["slippage_data_status:NO_SAMPLE"],
      },
    ],
    data_quality: {
      realized_pnl_confirmed: true,
      execution_sync_status: "COMPLETE",
      funding_sync_status: "COMPLETE",
      slippage_data_status: "COMPLETE",
      missing_close_execution_count: 0,
      warning_codes: null,
    },
    warnings: null,
  };

  const messages = costBreakdownWarningMessages(payload);
  const canonicalMessages = costBreakdownWarningMessages({
    ...payload,
    buckets: [],
    warnings: ["slippage_data_status:NO_SAMPLE"],
  });

  assert.deepEqual(messages, canonicalMessages);
  assert.equal(messages.length, 1);
});

test("bucket-only slippage reason infers no-sample publication when status is stale", async () => {
  const { costBreakdownBucketStatus, costBreakdownWarningMessages } = await costBreakdownModule;
  const payload = {
    period: "today" as const,
    timezone: "Asia/Seoul",
    start_at: "2026-05-27T00:00:00+09:00",
    end_at: "2026-05-28T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [
      {
        label: "today",
        start_at: "2026-05-27T00:00:00+09:00",
        end_at: "2026-05-28T00:00:00+09:00",
        net_pnl_usdt: 0,
        gross_pnl_usdt: 0,
        fee_usdt: 0,
        funding_usdt: 0,
        total_cost_usdt: 0,
        fee_ratio_pct: null,
        total_cost_ratio_pct: null,
        signed_slippage_bps: null,
        adverse_slippage_bps: null,
        slippage_data_status: null,
        slippage_data_reason: " NO_EXECUTION_SLIPPAGE_SAMPLE ",
        slippage_sample_count: 0,
        missing_slippage_sample_count: 0,
        warning_codes: null,
      },
    ],
    data_quality: {
      realized_pnl_confirmed: true,
      execution_sync_status: "COMPLETE",
      funding_sync_status: "COMPLETE",
      slippage_data_status: "COMPLETE",
      missing_close_execution_count: 0,
      warning_codes: null,
    },
    warnings: null,
  };
  const canonicalPayload = {
    ...payload,
    buckets: [
      {
        ...payload.buckets[0],
        slippage_data_status: "NO_SAMPLE",
        slippage_data_reason: "no_execution_slippage_sample",
      },
    ],
    warnings: ["slippage_data_status:NO_SAMPLE"],
  };

  assert.equal(
    costBreakdownBucketStatus(payload.buckets[0], payload.data_quality),
    costBreakdownBucketStatus(canonicalPayload.buckets[0], canonicalPayload.data_quality),
  );
  assert.deepEqual(costBreakdownWarningMessages(payload), costBreakdownWarningMessages(canonicalPayload));
});

test("warnings normalize and synthesize funding sync status", async () => {
  const { costBreakdownWarningMessages } = await costBreakdownModule;
  const basePayload = {
    period: "today" as const,
    timezone: "Asia/Seoul",
    start_at: "2026-05-26T00:00:00+09:00",
    end_at: "2026-05-27T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [],
    data_quality: {
      realized_pnl_confirmed: true,
      execution_sync_status: "COMPLETE",
      funding_sync_status: "STALE",
      slippage_data_status: "COMPLETE",
      missing_close_execution_count: 0,
    },
  };

  assert.deepEqual(
    costBreakdownWarningMessages({
      ...basePayload,
      warnings: [" funding_sync_status:stale ", "funding_sync_status:STALE"],
    }),
    ["펀딩비 동기화가 오래되어 비용 합계를 확정할 수 없습니다."],
  );
  assert.deepEqual(costBreakdownWarningMessages({ ...basePayload, warnings: null }), [
    "펀딩비 동기화가 오래되어 비용 합계를 확정할 수 없습니다.",
  ]);
});

test("warnings distinguish unknown slippage status from missing samples", async () => {
  const { costBreakdownWarningMessages, profitabilityCostWarningLabel } = await costBreakdownModule;

  const messages = costBreakdownWarningMessages({
    period: "today",
    timezone: "Asia/Seoul",
    start_at: "2026-05-26T00:00:00+09:00",
    end_at: "2026-05-27T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [],
    data_quality: {
      realized_pnl_confirmed: true,
      execution_sync_status: "COMPLETE",
      funding_sync_status: "COMPLETE",
      slippage_data_status: "UNKNOWN",
      missing_close_execution_count: 0,
    },
    warnings: ["slippage_data_status:UNKNOWN"],
  });

  assert.deepEqual(messages, [profitabilityCostWarningLabel("slippage_data_status:UNKNOWN")]);
});

test("warnings align slippage publication copy with profitability labels", async () => {
  const { costBreakdownWarningMessages, profitabilityCostWarningLabel } = await costBreakdownModule;
  const basePayload = {
    period: "today" as const,
    timezone: "Asia/Seoul",
    start_at: "2026-05-28T00:00:00+09:00",
    end_at: "2026-05-29T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [],
    data_quality: {
      realized_pnl_confirmed: true,
      execution_sync_status: "COMPLETE",
      funding_sync_status: "COMPLETE",
      slippage_data_status: "COMPLETE",
      missing_close_execution_count: 0,
    },
  };

  for (const status of ["NO_SAMPLE", "UNKNOWN", "NOT_READY", "BLOCKED"]) {
    assert.deepEqual(
      costBreakdownWarningMessages({
        ...basePayload,
        data_quality: {
          ...basePayload.data_quality,
          slippage_data_status: status,
        },
        warnings: [`slippage_data_status:${status}`],
      }),
      [profitabilityCostWarningLabel(`slippage_data_status:${status}`)],
    );
  }
});

test("warning tone escalates blocked slippage publication without escalating no-sample", async () => {
  const { costBreakdownWarningTone } = await costBreakdownModule;
  const basePayload = {
    period: "today" as const,
    timezone: "Asia/Seoul",
    start_at: "2026-05-28T00:00:00+09:00",
    end_at: "2026-05-29T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [],
    data_quality: {
      realized_pnl_confirmed: true,
      execution_sync_status: "COMPLETE",
      funding_sync_status: "COMPLETE",
      slippage_data_status: "COMPLETE",
      missing_close_execution_count: 0,
    },
    warnings: [],
  };

  assert.equal(
    costBreakdownWarningTone({
      ...basePayload,
      data_quality: { ...basePayload.data_quality, slippage_data_status: "NO_SAMPLE" },
      warnings: ["slippage_data_status:NO_SAMPLE"],
    }),
    "warn",
  );

  for (const status of ["UNKNOWN", "NOT_READY", "BLOCKED"]) {
    assert.equal(
      costBreakdownWarningTone({
        ...basePayload,
        data_quality: { ...basePayload.data_quality, slippage_data_status: status },
        warnings: [`slippage_data_status:${status}`],
      }),
      "danger",
    );
  }
  assert.equal(
    costBreakdownWarningTone({
      ...basePayload,
      buckets: [
        {
          label: "2026-05-28",
          start_at: "2026-05-28T00:00:00+09:00",
          end_at: "2026-05-29T00:00:00+09:00",
          net_pnl_usdt: 0,
          gross_pnl_usdt: 0,
          fee_usdt: 0,
          funding_usdt: 0,
          total_cost_usdt: 0,
          fee_ratio_pct: null,
          total_cost_ratio_pct: null,
          signed_slippage_bps: null,
          adverse_slippage_bps: null,
          warning_codes: ["slippage_data_status:BLOCKED"],
        },
      ],
    }),
    "danger",
  );
  assert.equal(
    costBreakdownWarningTone({
      ...basePayload,
      data_quality: { ...basePayload.data_quality, slippage_data_status: "INCOMPLETE" },
      warnings: ["slippage_data_status:INCOMPLETE"],
    }),
    "warn",
  );

  assert.equal(
    costBreakdownWarningTone({
      ...basePayload,
      data_quality: { ...basePayload.data_quality, funding_sync_status: "STALE" },
      warnings: ["funding_sync_status:STALE"],
    }),
    "warn",
  );
  assert.equal(costBreakdownWarningTone(basePayload), "good");
  assert.equal(
    costBreakdownWarningTone({
      ...basePayload,
      summary: { ...basePayload.summary, fee_ratio_pct: 15 },
    }),
    "warn",
  );
});

test("warnings synthesize stale runtime slippage status when warning list is null", async () => {
  const { costBreakdownWarningMessages } = await costBreakdownModule;
  const payload = {
    period: "today" as const,
    timezone: "Asia/Seoul",
    start_at: "2026-05-26T00:00:00+09:00",
    end_at: "2026-05-27T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [],
    data_quality: {
      realized_pnl_confirmed: true,
      execution_sync_status: "COMPLETE",
      funding_sync_status: "COMPLETE",
      slippage_data_status: "UNKNOWN",
      slippage_data_reason: null,
      missing_close_execution_count: 0,
    },
    warnings: null,
  };

  assert.deepEqual(
    costBreakdownWarningMessages(payload),
    costBreakdownWarningMessages({ ...payload, warnings: ["slippage_data_status:UNKNOWN"] }),
  );
});

test("warnings normalize stale runtime slippage status casing", async () => {
  const { costBreakdownWarningMessages, normalizeSlippageDataStatus } = await costBreakdownModule;
  const payload = {
    period: "today" as const,
    timezone: "Asia/Seoul",
    start_at: "2026-05-26T00:00:00+09:00",
    end_at: "2026-05-27T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [],
    data_quality: {
      realized_pnl_confirmed: true,
      execution_sync_status: "COMPLETE",
      funding_sync_status: "COMPLETE",
      slippage_data_status: "not_ready",
      slippage_data_reason: null,
      missing_close_execution_count: 0,
    },
    warnings: ["slippage_data_status:not_ready"],
  };

  assert.equal(normalizeSlippageDataStatus(" blocked "), "BLOCKED");
  assert.deepEqual(
    costBreakdownWarningMessages(payload),
    costBreakdownWarningMessages({
      ...payload,
      data_quality: { ...payload.data_quality, slippage_data_status: "NOT_READY" },
      warnings: ["slippage_data_status:NOT_READY"],
    }),
  );
});

test("warnings normalize execution sync status publication", async () => {
  const { costBreakdownWarningMessages, costBreakdownWarningTone, profitabilityCostWarningLabel } =
    await costBreakdownModule;
  const payload = {
    period: "today" as const,
    timezone: "Asia/Seoul",
    start_at: "2026-05-26T00:00:00+09:00",
    end_at: "2026-05-27T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [],
    data_quality: {
      realized_pnl_confirmed: true,
      execution_sync_status: " incomplete ",
      funding_sync_status: "COMPLETE",
      slippage_data_status: "COMPLETE",
      missing_close_execution_count: 0,
      warning_codes: ["execution_sync_status:incomplete"],
    },
    warnings: null,
  };

  assert.deepEqual(costBreakdownWarningMessages(payload), [
    profitabilityCostWarningLabel("execution_sync_status:INCOMPLETE"),
  ]);
  assert.equal(costBreakdownWarningTone(payload), "warn");
  assert.equal(
    costBreakdownWarningTone({
      ...payload,
      data_quality: { ...payload.data_quality, execution_sync_status: "UNKNOWN", warning_codes: null },
      warnings: ["execution_sync_status:unknown"],
    }),
    "danger",
  );
});

test("warnings translate cost asset conversion gaps from backend codes", async () => {
  const { costBreakdownWarningMessages, profitabilityCostWarningLabel } = await costBreakdownModule;

  const payload = {
    period: "today" as const,
    timezone: "Asia/Seoul",
    start_at: "2026-05-26T00:00:00+09:00",
    end_at: "2026-05-27T00:00:00+09:00",
    summary: {
      net_pnl_usdt: 0,
      gross_pnl_usdt: 0,
      fee_usdt: 0,
      funding_usdt: 0,
      total_cost_usdt: 0,
      fee_ratio_pct: null,
      total_cost_ratio_pct: null,
      signed_slippage_bps: null,
      adverse_slippage_bps: null,
    },
    buckets: [],
    data_quality: {
      realized_pnl_confirmed: true,
      execution_sync_status: "COMPLETE",
      funding_sync_status: "COMPLETE",
      slippage_data_status: "COMPLETE",
      missing_close_execution_count: 0,
    },
    warnings: ["fee_asset_conversion_unavailable:BNB", "funding_asset_conversion_unavailable:BTC"],
  };
  const messages = costBreakdownWarningMessages(payload);

  assert.deepEqual(messages, [
    profitabilityCostWarningLabel("fee_asset_conversion_unavailable:BNB"),
    profitabilityCostWarningLabel("funding_asset_conversion_unavailable:BTC"),
  ]);
  assert.deepEqual(
    costBreakdownWarningMessages({
      ...payload,
      warnings: [
        " Fee_Asset_Conversion_Unavailable: bnb ",
        "fee_asset_conversion_unavailable:BNB",
        " Funding_Asset_Conversion_Unavailable: btc ",
        "funding_asset_conversion_unavailable:BTC",
      ],
    }),
    messages,
  );
});

test("profitability cost status keeps no-data slippage gaps visible", async () => {
  const {
    profitabilityCostHasNoDataStatus,
    profitabilityCostStatusLabel,
    profitabilityCostTone,
    profitabilityCostWarningCodes,
  } =
    await costBreakdownModule;

  const completeNoDataCost = {
    status: "no_data",
    slippage_data_status: "COMPLETE",
    warning_codes: [],
    net_pnl: 0,
  };

  assert.equal(profitabilityCostHasNoDataStatus(" NO_DATA "), true);
  assert.equal(profitabilityCostHasNoDataStatus("complete"), false);
  assert.equal(
    profitabilityCostStatusLabel({
      ...completeNoDataCost,
      status: " NO_DATA ",
    }),
    profitabilityCostStatusLabel(completeNoDataCost),
  );
  assert.equal(
    profitabilityCostTone({
      ...completeNoDataCost,
      status: " NO_DATA ",
    }),
    profitabilityCostTone(completeNoDataCost),
  );
  assert.equal(
    profitabilityCostStatusLabel({
      status: "no_data",
      slippage_data_status: "NO_SAMPLE",
      slippage_data_reason: "no_execution_slippage_sample",
      warning_codes: ["slippage_data_status:NO_SAMPLE"],
      net_pnl: 0,
    }),
    "데이터 확인 필요",
  );
  assert.equal(
    profitabilityCostTone({
      status: "no_data",
      slippage_data_status: "NO_SAMPLE",
      warning_codes: ["slippage_data_status:NO_SAMPLE"],
      net_pnl: 0,
    }),
    "warn",
  );
  assert.equal(
    profitabilityCostTone({
      status: " NO_DATA ",
      slippage_data_status: "NO_SAMPLE",
      warning_codes: ["slippage_data_status:NO_SAMPLE"],
      net_pnl: 0,
    }),
    "warn",
  );
  assert.equal(
    profitabilityCostStatusLabel({
      status: "no_data",
      slippage_data_status: null,
      warning_codes: [],
      net_pnl: 0,
    }),
    "데이터 확인 필요",
  );
  assert.deepEqual(
    profitabilityCostWarningCodes({
      status: "no_data",
      slippage_data_status: null,
      warning_codes: [],
      net_pnl: 0,
    }),
    ["slippage_data_status:UNKNOWN"],
  );
  assert.equal(
    profitabilityCostTone({
      status: "no_data",
      slippage_data_status: null,
      warning_codes: [],
      net_pnl: 0,
    }),
    "danger",
  );
  assert.deepEqual(
    profitabilityCostWarningCodes({
      status: "no_data",
      slippage_data_status: "",
      warning_codes: null,
      net_pnl: 0,
    }),
    ["slippage_data_status:UNKNOWN"],
  );
  const staleReasonOnlyCost = {
    status: "no_data",
    slippage_data_status: null,
    slippage_data_reason: " no_execution_slippage_sample ",
    slippage_sample_count: 0,
    missing_slippage_sample_count: 0,
    warning_codes: null,
    net_pnl: 0,
  };
  assert.deepEqual(profitabilityCostWarningCodes(staleReasonOnlyCost), ["slippage_data_status:NO_SAMPLE"]);
  assert.deepEqual(
    profitabilityCostWarningCodes({
      ...staleReasonOnlyCost,
      slippage_data_status: "UNKNOWN",
      warning_codes: ["slippage_data_status:UNKNOWN"],
    }),
    ["slippage_data_status:NO_SAMPLE"],
  );
  assert.equal(
    profitabilityCostStatusLabel(staleReasonOnlyCost),
    profitabilityCostStatusLabel({
      status: "no_data",
      slippage_data_status: "NO_SAMPLE",
      slippage_data_reason: "no_execution_slippage_sample",
      warning_codes: ["slippage_data_status:NO_SAMPLE"],
      net_pnl: 0,
    }),
  );
  assert.equal(profitabilityCostTone(staleReasonOnlyCost), "warn");
  assert.deepEqual(
    profitabilityCostWarningCodes({
      status: "ok",
      slippage_data_status: null,
      slippage_data_reason: "slippage_blocked",
      warning_codes: null,
      net_pnl: 0,
    }),
    ["slippage_data_status:BLOCKED"],
  );
  assert.equal(
    profitabilityCostTone({
      status: "ok",
      slippage_data_status: null,
      slippage_data_reason: "slippage_blocked",
      warning_codes: null,
      net_pnl: 0,
    }),
    "danger",
  );
  assert.deepEqual(
    profitabilityCostWarningCodes({
      status: "no_data",
      slippage_data_status: "not_ready",
      warning_codes: null,
      net_pnl: 0,
    }),
    ["slippage_data_status:NOT_READY"],
  );
  assert.equal(
    profitabilityCostTone({
      status: "no_data",
      slippage_data_status: "not_ready",
      warning_codes: null,
      net_pnl: 0,
    }),
    "danger",
  );
  assert.deepEqual(
    profitabilityCostWarningCodes({
      status: "no_data",
      slippage_data_status: "NO_SAMPLE",
      warning_codes: ["slippage_data_status:NO_SAMPLE"],
      net_pnl: 0,
    }),
    ["slippage_data_status:NO_SAMPLE"],
  );
  assert.deepEqual(
    profitabilityCostWarningCodes({
      status: "ok",
      slippage_data_status: "COMPLETE",
      funding_sync_status: " stale ",
      warning_codes: null,
      net_pnl: 0,
    }),
    ["funding_sync_status:STALE"],
  );
  assert.deepEqual(
    profitabilityCostWarningCodes({
      status: "ok",
      slippage_data_status: "NO_SAMPLE",
      funding_sync_status: "STALE",
      warning_codes: null,
      net_pnl: 0,
    }),
    ["funding_sync_status:STALE", "slippage_data_status:NO_SAMPLE"],
  );
  assert.deepEqual(
    profitabilityCostWarningCodes({
      status: "ok",
      slippage_data_status: "COMPLETE",
      execution_sync_status: " incomplete ",
      warning_codes: null,
      net_pnl: 0,
    }),
    ["execution_sync_status:INCOMPLETE"],
  );
  assert.deepEqual(
    profitabilityCostWarningCodes({
      status: "ok",
      slippage_data_status: "not_ready",
      warning_codes: [
        "slippage_data_status:not_ready",
        "slippage_data_status:NOT_READY",
        " funding_sync_status:stale ",
      ],
      net_pnl: 0,
    }),
    ["slippage_data_status:NOT_READY", "funding_sync_status:STALE"],
  );
  assert.equal(
    profitabilityCostTone({
      status: "complete",
      slippage_data_status: "COMPLETE",
      warning_codes: [],
      net_pnl: 1,
    }),
    "safe",
  );
  assert.equal(
    profitabilityCostTone({
      status: "complete",
      slippage_data_status: "COMPLETE",
      warning_codes: ["positive_gross_negative_net"],
      net_pnl: -1,
    }),
    "danger",
  );
});

test("profitability publication status uses readiness when compact payload omits costs", async () => {
  const {
    profitabilityPublicationStatusLabel,
    profitabilityPublicationTone,
    profitabilityPublicationWarningCodes,
    profitabilityReadinessStatusLabel,
    profitabilityReadinessTone,
  } = await costBreakdownModule;

  const readiness = {
    status: "not_ready",
    reason_codes: [
      "insufficient_sample",
      "productization_profitability_unverified",
      "slippage_data_status:NO_SAMPLE",
    ],
  };

  const notReadyProfitabilityLabel = profitabilityPublicationStatusLabel(null, readiness);
  assert.equal(notReadyProfitabilityLabel, "제품화 수익성 검증 부족");
  assert.equal(profitabilityPublicationTone(null, readiness), "danger");
  assert.deepEqual(profitabilityPublicationWarningCodes(null, readiness), ["slippage_data_status:NO_SAMPLE"]);
  assert.deepEqual(
    profitabilityPublicationWarningCodes(null, {
      status: "not_ready",
      reason_codes: [
        " funding_sync_status:stale ",
        "slippage_data_status:not_ready",
        "slippage_data_status:NOT_READY",
        " execution_sync_status:incomplete ",
      ],
    }),
    ["funding_sync_status:STALE", "slippage_data_status:NOT_READY", "execution_sync_status:INCOMPLETE"],
  );
  const blockedProfitabilityLabel = profitabilityPublicationStatusLabel(null, {
    status: "blocked",
    reason_codes: [],
  });
  assert.equal(blockedProfitabilityLabel, "제품화 차단");
  assert.equal(
    profitabilityPublicationStatusLabel(null, {
      status: " NOT_READY ",
      reason_codes: [" productization_profitability_unverified "],
    }),
    notReadyProfitabilityLabel,
  );
  assert.equal(
    profitabilityReadinessStatusLabel({
      status: " NOT_READY ",
      reason_codes: [" productization_profitability_unverified "],
    }),
    notReadyProfitabilityLabel,
  );
  assert.equal(
    profitabilityPublicationTone(null, {
      status: " NOT_READY ",
      reason_codes: [" productization_profitability_unverified "],
    }),
    "danger",
  );
  assert.equal(
    profitabilityReadinessTone({
      status: " NOT_READY ",
      reason_codes: [" productization_profitability_unverified "],
    }),
    "danger",
  );
  assert.equal(
    profitabilityPublicationStatusLabel(null, {
      status: " BLOCKED ",
      reason_codes: [],
    }),
    blockedProfitabilityLabel,
  );
  assert.equal(
    profitabilityReadinessStatusLabel({
      status: " BLOCKED ",
      reason_codes: [],
    }),
    blockedProfitabilityLabel,
  );
  assert.equal(
    profitabilityPublicationTone(null, {
      status: " BLOCKED ",
      reason_codes: [],
    }),
    "danger",
  );
  assert.equal(
    profitabilityReadinessTone({
      status: " BLOCKED ",
      reason_codes: [],
    }),
    "danger",
  );
  assert.equal(
    profitabilityPublicationTone(null, {
      status: " LIMITED_LIVE_CANDIDATE ",
      reason_codes: [],
    }),
    "safe",
  );
  assert.equal(
    profitabilityPublicationStatusLabel(
      {
        status: "no_data",
        slippage_data_status: "NO_SAMPLE",
        warning_codes: ["slippage_data_status:NO_SAMPLE"],
        net_pnl: 0,
      },
      readiness,
    ),
    notReadyProfitabilityLabel,
  );
  assert.equal(
    profitabilityPublicationTone(
      {
        status: "no_data",
        slippage_data_status: "NO_SAMPLE",
        warning_codes: ["slippage_data_status:NO_SAMPLE"],
        net_pnl: 0,
      },
      readiness,
    ),
    "danger",
  );
  assert.deepEqual(
    profitabilityPublicationWarningCodes(
      {
        status: "no_data",
        slippage_data_status: "NO_SAMPLE",
        warning_codes: ["slippage_data_status:NO_SAMPLE"],
        net_pnl: 0,
      },
      readiness,
    ),
    ["slippage_data_status:NO_SAMPLE"],
  );
  assert.deepEqual(
    profitabilityPublicationWarningCodes(
      {
        status: "no_data",
        warning_codes: [],
        net_pnl: 0,
      },
      readiness,
    ),
    ["slippage_data_status:NO_SAMPLE"],
  );
  assert.equal(
    profitabilityPublicationTone(
      {
        status: "no_data",
        warning_codes: [],
        net_pnl: 0,
      },
      readiness,
    ),
    "danger",
  );
  assert.deepEqual(
    profitabilityPublicationWarningCodes(
      {
        status: "no_data",
        slippage_data_status: "UNKNOWN",
        warning_codes: ["slippage_data_status:UNKNOWN"],
        net_pnl: 0,
      },
      {
        status: "not_ready",
        reason_codes: [" slippage_data_status:not_ready "],
      },
    ),
    ["slippage_data_status:NOT_READY"],
  );
  assert.equal(
    profitabilityPublicationTone(
      {
        status: "no_data",
        slippage_data_status: "UNKNOWN",
        warning_codes: ["slippage_data_status:UNKNOWN"],
        net_pnl: 0,
      },
      {
        status: "not_ready",
        reason_codes: [" slippage_data_status:not_ready "],
      },
    ),
    "danger",
  );
  assert.deepEqual(
    profitabilityPublicationWarningCodes(
      {
        status: "complete",
        slippage_data_status: "COMPLETE",
        warning_codes: [],
        net_pnl: 1,
      },
      readiness,
    ),
    [],
  );
  assert.deepEqual(
    profitabilityPublicationWarningCodes(
      {
        status: "complete",
        slippage_data_status: "COMPLETE",
        execution_sync_status: "COMPLETE",
        warning_codes: [],
        net_pnl: 1,
      },
      {
        status: "not_ready",
        reason_codes: ["execution_sync_status:INCOMPLETE"],
      },
    ),
    [],
  );
  assert.deepEqual(
    profitabilityPublicationWarningCodes(
      {
        status: "complete",
        slippage_data_status: "COMPLETE",
        funding_sync_status: null,
        warning_codes: [],
        net_pnl: 1,
      },
      {
        status: "not_ready",
        reason_codes: ["funding_sync_status:stale", "slippage_data_status:NO_SAMPLE"],
      },
    ),
    ["funding_sync_status:STALE"],
  );
  assert.equal(
    profitabilityPublicationStatusLabel(
      {
        status: "complete",
        slippage_data_status: "COMPLETE",
        warning_codes: [],
        net_pnl: 1,
      },
      {
        status: "blocked",
        reason_codes: ["productization_profitability_unverified"],
      },
    ),
    blockedProfitabilityLabel,
  );
  assert.equal(
    profitabilityPublicationTone(
      {
        status: "complete",
        slippage_data_status: "COMPLETE",
        warning_codes: [],
        net_pnl: 1,
      },
      {
        status: "blocked",
        reason_codes: ["productization_profitability_unverified"],
      },
    ),
    "danger",
  );
  assert.equal(
    profitabilityPublicationTone(
      {
        status: "complete",
        slippage_data_status: "COMPLETE",
        execution_sync_status: null,
        warning_codes: [],
        net_pnl: 1,
      },
      {
        status: "watch",
        reason_codes: ["execution_sync_status:UNKNOWN"],
      },
    ),
    "danger",
  );
  assert.equal(
    profitabilityPublicationTone(
      {
        status: "complete",
        slippage_data_status: "COMPLETE",
        execution_sync_status: "UNKNOWN",
        warning_codes: [],
        net_pnl: 1,
      },
      {
        status: "watch",
        reason_codes: [],
      },
    ),
    "danger",
  );
  assert.equal(
    profitabilityPublicationTone(
      {
        status: "complete",
        slippage_data_status: "COMPLETE",
        execution_sync_status: null,
        warning_codes: [],
        net_pnl: 1,
      },
      {
        status: "watch",
        reason_codes: ["execution_sync_status:INCOMPLETE"],
      },
    ),
    "warn",
  );
});

test("profitability warning labels keep not-ready and blocked slippage readable", async () => {
  const { profitabilityCostWarningLabel } = await costBreakdownModule;

  assert.equal(
    profitabilityCostWarningLabel(" Fee_Asset_Conversion_Unavailable: bnb "),
    profitabilityCostWarningLabel("fee_asset_conversion_unavailable:BNB"),
  );
  assert.equal(
    profitabilityCostWarningLabel(" Funding_Asset_Conversion_Unavailable: btc "),
    profitabilityCostWarningLabel("funding_asset_conversion_unavailable:BTC"),
  );
  assert.equal(
    profitabilityCostWarningLabel("slippage_data_status:NOT_READY"),
    "슬리피지 준비 상태가 부족해 평균 체결 불리도를 확정할 수 없습니다.",
  );
  assert.equal(
    profitabilityCostWarningLabel("slippage_data_status:BLOCKED"),
    "슬리피지 산출이 차단되어 평균 체결 불리도를 확정할 수 없습니다.",
  );
  assert.equal(
    profitabilityCostWarningLabel("slippage_data_status:not_ready"),
    "슬리피지 준비 상태가 부족해 평균 체결 불리도를 확정할 수 없습니다.",
  );
  assert.equal(profitabilityCostWarningLabel("unknown_warning_code"), "unknown_warning_code");
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

test("bucket status normalizes funding sync status before marking data quality", async () => {
  const { costBreakdownBucketStatus } = await costBreakdownModule;
  const bucket = {
    label: "2026-05-26",
    start_at: "2026-05-26T00:00:00+09:00",
    end_at: "2026-05-27T00:00:00+09:00",
    net_pnl_usdt: 1,
    gross_pnl_usdt: 1,
    fee_usdt: 0,
    funding_usdt: 0,
    total_cost_usdt: 0,
    fee_ratio_pct: null,
    total_cost_ratio_pct: null,
    signed_slippage_bps: 0,
    adverse_slippage_bps: 0,
    slippage_data_status: "complete",
  };
  const baseDataQuality = {
    realized_pnl_confirmed: true,
    execution_sync_status: "COMPLETE",
    funding_sync_status: "COMPLETE",
    slippage_data_status: "complete",
    missing_close_execution_count: 0,
  };

  assert.equal(
    costBreakdownBucketStatus(bucket, { ...baseDataQuality, funding_sync_status: " complete " }),
    costBreakdownBucketStatus(bucket, baseDataQuality),
  );
});

test("bucket status uses bucket slippage publication when period quality is complete", async () => {
  const { costBreakdownBucketSlippageStatus, costBreakdownBucketStatus, slippageDataQualityLabel } =
    await costBreakdownModule;
  const bucket = {
    label: "2026-05-02",
    start_at: "2026-05-02T00:00:00+09:00",
    end_at: "2026-05-03T00:00:00+09:00",
    net_pnl_usdt: 0,
    gross_pnl_usdt: 0,
    fee_usdt: 0,
    funding_usdt: 0,
    total_cost_usdt: 0,
    fee_ratio_pct: null,
    total_cost_ratio_pct: null,
    signed_slippage_bps: null,
    adverse_slippage_bps: null,
    slippage_data_status: "NO_SAMPLE",
    slippage_data_reason: "no_execution_slippage_sample",
    slippage_sample_count: 0,
    missing_slippage_sample_count: 0,
  };

  const status = costBreakdownBucketStatus(bucket, {
    realized_pnl_confirmed: true,
    execution_sync_status: "COMPLETE",
    funding_sync_status: "COMPLETE",
    slippage_data_status: "COMPLETE",
    missing_close_execution_count: 0,
  });

  assert.equal(
    costBreakdownBucketSlippageStatus(bucket, {
      slippage_data_status: "COMPLETE",
    }),
    "NO_SAMPLE",
  );
  assert.equal(
    costBreakdownBucketSlippageStatus(
      { slippage_data_status: null },
      {
        slippage_data_status: "UNKNOWN",
      },
    ),
    "UNKNOWN",
  );
  assert.equal(status, slippageDataQualityLabel(bucket));
});

test("bucket status falls back to period slippage reason when stale bucket fields are null", async () => {
  const { costBreakdownBucketSlippageStatus, costBreakdownBucketStatus, slippageDataQualityLabel } =
    await costBreakdownModule;
  const dataQuality = {
    realized_pnl_confirmed: true,
    execution_sync_status: "COMPLETE",
    funding_sync_status: "COMPLETE",
    slippage_data_status: "NO_SAMPLE",
    slippage_data_reason: "no_execution_slippage_sample",
    slippage_sample_count: 0,
    missing_slippage_sample_count: 0,
    missing_close_execution_count: 0,
  };
  const bucket = {
    label: "2026-05-26",
    start_at: "2026-05-26T00:00:00+09:00",
    end_at: "2026-05-27T00:00:00+09:00",
    net_pnl_usdt: 0,
    gross_pnl_usdt: 0,
    fee_usdt: 0,
    funding_usdt: 0,
    total_cost_usdt: 0,
    fee_ratio_pct: null,
    total_cost_ratio_pct: null,
    signed_slippage_bps: null,
    adverse_slippage_bps: null,
    slippage_data_status: null,
    slippage_data_reason: null,
    slippage_sample_count: null,
    missing_slippage_sample_count: null,
  };

  assert.equal(costBreakdownBucketStatus(bucket, dataQuality), slippageDataQualityLabel(dataQuality));

  const completeDataQuality = {
    ...dataQuality,
    slippage_data_status: "COMPLETE",
    slippage_data_reason: null,
    slippage_sample_count: 7,
  };
  const blankStatusBucket = {
    ...bucket,
    slippage_data_status: "   ",
  };

  assert.equal(costBreakdownBucketSlippageStatus(blankStatusBucket, completeDataQuality), "COMPLETE");
  assert.equal(costBreakdownBucketStatus(blankStatusBucket, completeDataQuality), "정상");
});

test("bucket status keeps bucket slippage reason visible when period quality is incomplete", async () => {
  const { costBreakdownBucketStatus, slippageDataQualityLabel } = await costBreakdownModule;
  const bucket = {
    label: "2026-05-02",
    start_at: "2026-05-02T00:00:00+09:00",
    end_at: "2026-05-03T00:00:00+09:00",
    net_pnl_usdt: 0,
    gross_pnl_usdt: 0,
    fee_usdt: 0,
    funding_usdt: 0,
    total_cost_usdt: 0,
    fee_ratio_pct: null,
    total_cost_ratio_pct: null,
    signed_slippage_bps: null,
    adverse_slippage_bps: null,
    slippage_data_status: "NO_SAMPLE",
    slippage_data_reason: "no_execution_slippage_sample",
    slippage_sample_count: 0,
    missing_slippage_sample_count: 0,
  };

  const status = costBreakdownBucketStatus(bucket, {
    realized_pnl_confirmed: true,
    execution_sync_status: "COMPLETE",
    funding_sync_status: "COMPLETE",
    slippage_data_status: "INCOMPLETE",
    slippage_data_reason: "missing_execution_slippage_sample",
    slippage_sample_count: 1,
    missing_slippage_sample_count: 1,
    missing_close_execution_count: 0,
  });

  assert.equal(status, slippageDataQualityLabel(bucket));
});
