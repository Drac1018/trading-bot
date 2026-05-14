import assert from "node:assert/strict";
import test from "node:test";

type MarketTimeframesModule = typeof import("./market-timeframes");

const marketTimeframesModule = import(
  new URL("./market-timeframes.ts", import.meta.url).href,
) as Promise<MarketTimeframesModule>;

test("compact feature metadata enables 1h and 4h without raw payload", async () => {
  const { resolveAvailableMarketTimeframes, resolveEffectiveMarketTimeframe } = await marketTimeframesModule;
  const availability = resolveAvailableMarketTimeframes(
    [{ symbol: "BTCUSDT", timeframe: "15m" }],
    [{ symbol: "BTCUSDT", timeframe: "15m", available_timeframes: ["15m", "1h", "4h"] }],
  );

  assert.equal(availability.get("1h")?.enabled, true);
  assert.equal(availability.get("4h")?.enabled, true);
  assert.equal(resolveEffectiveMarketTimeframe("1h", availability), "1h");
  assert.equal(resolveEffectiveMarketTimeframe("4h", availability), "4h");
});

test("compact feature metadata keeps unsupported higher timeframes disabled", async () => {
  const { resolveAvailableMarketTimeframes, resolveEffectiveMarketTimeframe } = await marketTimeframesModule;
  const availability = resolveAvailableMarketTimeframes(
    [{ symbol: "BTCUSDT", timeframe: "15m" }],
    [{ symbol: "BTCUSDT", timeframe: "15m", available_timeframes: ["15m"] }],
  );

  assert.equal(availability.get("15m")?.enabled, true);
  assert.equal(availability.get("1h")?.enabled, false);
  assert.equal(availability.get("4h")?.enabled, false);
  assert.equal(resolveEffectiveMarketTimeframe("1h", availability), "15m");
});

test("legacy full feature payload still exposes multi_timeframe keys", async () => {
  const { resolveAvailableMarketTimeframes } = await marketTimeframesModule;
  const availability = resolveAvailableMarketTimeframes(
    [{ symbol: "BTCUSDT", timeframe: "15m" }],
    [
      {
        symbol: "BTCUSDT",
        timeframe: "15m",
        payload: { multi_timeframe: { "1h": { trend_score: 1.1 }, "4h": { trend_score: 0.8 } } },
      },
    ],
  );

  assert.equal(availability.get("1h")?.enabled, true);
  assert.equal(availability.get("4h")?.enabled, true);
});
