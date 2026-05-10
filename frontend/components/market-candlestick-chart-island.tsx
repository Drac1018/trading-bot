"use client";

import dynamic from "next/dynamic";

import type { ClientMarketCandlestickModel } from "./market-candlestick-chart-client";

const MarketCandlestickChartNoSsr = dynamic(
  () =>
    import("./market-candlestick-chart-client").then((module) => module.MarketCandlestickChartClient),
  {
    ssr: false,
    loading: () => (
      <div className="flex min-h-[548px] items-center justify-center rounded-md border border-slate-200 bg-white text-sm text-slate-500">
        차트 로딩 중
      </div>
    ),
  },
);

export function MarketCandlestickChartIsland({
  model,
  compact = false,
}: {
  model: ClientMarketCandlestickModel;
  compact?: boolean;
}) {
  return <MarketCandlestickChartNoSsr model={model} compact={compact} />;
}
