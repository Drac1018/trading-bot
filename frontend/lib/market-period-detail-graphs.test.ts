import assert from "node:assert/strict";
import test from "node:test";

type MarketPeriodDetailGraphsModule = typeof import("./market-period-detail-graphs");

const marketPeriodDetailGraphsModule = import(
  new URL("./market-period-detail-graphs.ts", import.meta.url).href,
) as Promise<MarketPeriodDetailGraphsModule>;
type MarketDashboardCopyModule = typeof import("./market-dashboard-copy");
const marketDashboardCopyModule = import(
  new URL("./market-dashboard-copy.ts", import.meta.url).href,
) as Promise<MarketDashboardCopyModule>;

const candles = [
  { timestamp: "2026-05-10T00:00:00", close: 2300 },
  { timestamp: "2026-05-10T00:15:00", close: 2310 },
  { timestamp: "2026-05-10T00:30:00", close: 2295 },
  { timestamp: "2026-05-10T00:45:00", close: 2325 },
];

test("marketPriceTrendStats summarizes selected candles for the price graph", async () => {
  const { marketPriceTrendStats } = await marketPeriodDetailGraphsModule;

  assert.deepEqual(marketPriceTrendStats(candles), {
    count: 4,
    minPrice: 2295,
    maxPrice: 2325,
    firstTimestamp: "2026-05-10T00:00:00",
    lastTimestamp: "2026-05-10T00:45:00",
  });
});

test("buildMarketBlockedReasonDistribution counts Korean labels inside the selected candle period", async () => {
  const { buildMarketBlockedReasonDistribution } = await marketPeriodDetailGraphsModule;
  const { marketReasonCodeLabel } = await marketDashboardCopyModule;
  const distribution = buildMarketBlockedReasonDistribution({
    symbol: "ETHUSDT",
    candles,
    reasonLabeler: marketReasonCodeLabel,
    markers: [
      {
        timestamp: "2026-05-10T00:05:00",
        kind: "risk_blocked",
        label: "blocked",
        detail: "risk blocked",
        symbol: "ETHUSDT",
        action: "long",
        price: 2301,
        statusLabel: "risk blocked",
        reasonLabel: "stale_market_data, exposure_limit_exceeded",
        reasonCodes: ["stale_market_data", "exposure_limit_exceeded"],
        sourceId: "risk:1",
      },
      {
        timestamp: "2026-05-10T00:20:00",
        kind: "risk_blocked",
        label: "blocked",
        detail: "risk blocked",
        symbol: "ETHUSDT",
        action: "long",
        price: 2302,
        statusLabel: "risk blocked",
        reasonLabel: "stale_market_data",
        reasonCodes: ["stale_market_data"],
        sourceId: "risk:2",
      },
      {
        timestamp: "2026-05-10T00:25:00",
        kind: "risk_approved",
        label: "approved",
        detail: "risk approved",
        symbol: "ETHUSDT",
        action: "long",
        price: 2303,
        statusLabel: "risk approved",
        reasonLabel: null,
        sourceId: "risk:3",
      },
      {
        timestamp: "2026-05-10T00:25:00",
        kind: "risk_blocked",
        label: "blocked",
        detail: "risk blocked",
        symbol: "BTCUSDT",
        action: "long",
        price: 100000,
        statusLabel: "risk blocked",
        reasonLabel: "stale_market_data",
        reasonCodes: ["stale_market_data"],
        sourceId: "risk:4",
      },
      {
        timestamp: "2026-05-10T02:00:00",
        kind: "risk_blocked",
        label: "blocked",
        detail: "risk blocked",
        symbol: "ETHUSDT",
        action: "long",
        price: 2304,
        statusLabel: "risk blocked",
        reasonLabel: "stale_market_data",
        reasonCodes: ["stale_market_data"],
        sourceId: "risk:5",
      },
    ],
  });

  assert.deepEqual(
    distribution.map((item) => ({ label: item.label, count: item.count, codes: item.codes })),
    [
      {
        label: "시장 데이터가 오래되어 신규 진입을 막았습니다",
        count: 2,
        codes: ["stale_market_data"],
      },
      {
        label: "노출 한도를 초과해 신규 진입을 막았습니다",
        count: 1,
        codes: ["exposure_limit_exceeded"],
      },
    ],
  );
});

test("buildMarketBlockedReasonDistribution returns an empty set when no blocked reason data exists", async () => {
  const { buildMarketBlockedReasonDistribution } = await marketPeriodDetailGraphsModule;
  const { marketReasonCodeLabel } = await marketDashboardCopyModule;

  assert.deepEqual(
    buildMarketBlockedReasonDistribution({
      symbol: "ETHUSDT",
      candles,
      reasonLabeler: marketReasonCodeLabel,
      markers: [
        {
          timestamp: "2026-05-10T00:05:00",
          kind: "risk_approved",
          label: "approved",
          detail: "risk approved",
          symbol: "ETHUSDT",
          action: "long",
          price: 2301,
          statusLabel: "risk approved",
          reasonLabel: null,
          sourceId: "risk:1",
        },
      ],
    }),
    [],
  );
});
