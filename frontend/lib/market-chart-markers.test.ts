import assert from "node:assert/strict";
import test from "node:test";

type MarketChartMarkersModule = typeof import("./market-chart-markers");

const marketChartMarkersModule = import(
  new URL("./market-chart-markers.ts", import.meta.url).href,
) as Promise<MarketChartMarkersModule>;

test("market chart marker rows keep AI, risk, and execution markers distinct", async () => {
  const { buildMarketChartEventMarkersFromRows } = await marketChartMarkersModule;
  const markers = buildMarketChartEventMarkersFromRows("ETHUSDT", {
    decisions: [
      {
        id: 101,
        symbol: "ETHUSDT",
        decision: "long",
        confidence: 0.72,
        created_at: "2026-05-10T01:00:00",
        metadata_json: { last_ai_invoked_at: "2026-05-10T01:01:00" },
        output_payload: { symbol: "ETHUSDT", decision: "long", confidence: 0.72 },
      },
    ],
    riskChecks: [
      {
        id: 202,
        symbol: "ETHUSDT",
        allowed: false,
        decision: "long",
        created_at: "2026-05-10T01:02:00",
        blocked_reason_codes: ["ENTRY_TRIGGER_NOT_MET"],
        pending_entry_plan: {
          entry_zone_min: 2319,
          entry_zone_max: 2321,
        },
      },
    ],
    orders: [],
    executions: [
      {
        id: 303,
        order_id: 404,
        symbol: "ETHUSDT",
        status: "filled",
        order_status: "filled",
        order_type: "limit",
        fill_price: 2322.25,
        created_at: "2026-05-10T01:03:00",
        payload: { side: "SELL" },
      },
    ],
  });

  assert.deepEqual(
    markers.map((marker) => marker.kind),
    ["ai", "risk_blocked", "execution"],
  );
  assert.equal(markers[0]?.statusLabel, "AI 추천");
  assert.equal(markers[0]?.action, "롱 진입 제안");
  assert.equal(markers[1]?.statusLabel, "리스크 차단");
  assert.match(markers[1]?.reasonLabel ?? "", /진입 조건/);
  assert.match(markers[1]?.reasonLabel ?? "", /원본: ENTRY_TRIGGER_NOT_MET/);
  assert.equal(markers[1]?.price, 2320);
  assert.equal(markers[2]?.statusLabel, "실제 실행");
  assert.equal(markers[2]?.price, 2322.25);
});

test("market chart visible marker count drives the empty state", async () => {
  const {
    buildMarketChartEventMarkersFromRows,
    countVisibleMarketChartMarkers,
    emptyMarketChartMarkerRows,
  } = await marketChartMarkersModule;
  const emptyMarkers = buildMarketChartEventMarkersFromRows("ETHUSDT", emptyMarketChartMarkerRows());
  assert.equal(emptyMarkers.length, 0);
  assert.equal(
    countVisibleMarketChartMarkers(emptyMarkers, [{ timestamp: "2026-05-10T01:00:00" }]),
    0,
  );

  const markers = buildMarketChartEventMarkersFromRows("ETHUSDT", {
    ...emptyMarketChartMarkerRows(),
    riskChecks: [
      {
        id: 202,
        symbol: "ETHUSDT",
        allowed: true,
        decision: "short",
        created_at: "2026-05-10T01:15:00",
      },
    ],
  });

  assert.equal(
    countVisibleMarketChartMarkers(markers, [
      { timestamp: "2026-05-10T01:00:00" },
      { timestamp: "2026-05-10T01:30:00" },
    ]),
    1,
  );
});

test("market chart markers do not treat AI hold rows as trade positions", async () => {
  const { buildMarketChartEventMarkersFromRows, emptyMarketChartMarkerRows } = await marketChartMarkersModule;
  const markers = buildMarketChartEventMarkersFromRows("ETHUSDT", {
    ...emptyMarketChartMarkerRows(),
    decisions: [
      {
        id: 101,
        symbol: "ETHUSDT",
        decision: "hold",
        created_at: "2026-05-10T01:00:00",
      },
    ],
    riskChecks: [
      {
        id: 202,
        symbol: "ETHUSDT",
        allowed: false,
        decision: "hold",
        created_at: "2026-05-10T01:01:00",
        blocked_reason_codes: ["HOLD_DECISION"],
      },
    ],
  });

  assert.deepEqual(markers, []);
});
