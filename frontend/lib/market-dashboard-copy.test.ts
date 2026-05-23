import assert from "node:assert/strict";
import test from "node:test";

type MarketDashboardCopyModule = typeof import("./market-dashboard-copy");

const marketDashboardCopyModule = import(
  new URL("./market-dashboard-copy.ts", import.meta.url).href,
) as Promise<MarketDashboardCopyModule>;

test("market data source and status labels are operator-facing Korean", async () => {
  const { marketDataSourceLabel, marketDataStatusLabel } = await marketDashboardCopyModule;

  assert.equal(marketDataSourceLabel("binance_ws_final_kline"), "Binance Futures 실시간 완성 캔들 데이터");
  assert.equal(marketDataSourceLabel("binance_rest"), "Binance Futures 최근 캔들 데이터");
  assert.equal(marketDataStatusLabel("fresh"), "정상");
  assert.equal(marketDataStatusLabel("stale"), "오래됨");
  assert.equal(marketDataStatusLabel("incomplete"), "불완전");
  assert.equal(marketDataStatusLabel("unavailable"), "사용할 수 없음");
});

test("market decision labels do not expose internal action codes", async () => {
  const { marketDecisionLabel } = await marketDashboardCopyModule;

  assert.equal(marketDecisionLabel("enter_long"), "롱 진입 제안");
  assert.equal(marketDecisionLabel("enter_short"), "숏 진입 제안");
  assert.equal(marketDecisionLabel("hold"), "관망");
  assert.equal(marketDecisionLabel("reduce"), "포지션 축소");
  assert.equal(marketDecisionLabel("exit"), "청산");
});

test("market block reason labels cover representative and unknown reason codes", async () => {
  const {
    formatMarketRawReasonCodes,
    formatMarketReasonCodeDebugLabels,
    formatMarketReasonCodeLabels,
    marketReasonCodeLabel,
  } = await marketDashboardCopyModule;

  assert.equal(marketReasonCodeLabel("stale_market_data"), "시장 데이터가 오래되어 신규 진입을 막았습니다");
  assert.equal(
    marketReasonCodeLabel("market_stream_cache_unavailable"),
    "실시간 시장 데이터 캐시를 사용할 수 없어 REST 보조 경로를 사용 중입니다",
  );
  assert.equal(
    marketReasonCodeLabel("market_stream_older_than_rest"),
    "실시간 스트림보다 REST 최신 캔들이 더 최신이라 REST 보조 경로를 사용 중입니다",
  );
  assert.equal(
    marketReasonCodeLabel("MARKET_STREAM_CONNECTION_DROPPED"),
    "실시간 시장 데이터 스트림 연결이 끊겼습니다",
  );
  assert.equal(marketReasonCodeLabel("incomplete_market_data"), "시장 데이터가 불완전하여 신규 진입을 막았습니다");
  assert.equal(marketReasonCodeLabel("exposure_limit_exceeded"), "노출 한도를 초과해 신규 진입을 막았습니다");
  assert.equal(
    marketReasonCodeLabel("DERIVATIVES_ALIGNMENT_HEADWIND"),
    "파생시장 정합성이 진입 방향을 뒷받침하지 않습니다",
  );
  assert.equal(
    marketReasonCodeLabel("BREAKOUT_OI_SPREAD_FILTER"),
    "돌파처럼 보여도 OI와 스프레드 조건이 부족합니다",
  );
  assert.equal(
    marketReasonCodeLabel("ROLE_DAILY_TOKEN_BUDGET_EXHAUSTED"),
    "trading_decision 일일 AI 토큰 예산이 소진되어 deterministic 판단을 사용했습니다",
  );
  assert.equal(marketReasonCodeLabel("unknown_reason_code"), "알 수 없는 차단 사유");
  assert.equal(formatMarketReasonCodeLabels(["unknown_reason_code"]), "알 수 없는 차단 사유");
  assert.equal(
    formatMarketReasonCodeDebugLabels(["unknown_reason_code"]),
    "알 수 없는 차단 사유 (원본: unknown_reason_code)",
  );
  assert.equal(formatMarketRawReasonCodes(["stale_market_data"]), "원본 코드: stale_market_data");
});
