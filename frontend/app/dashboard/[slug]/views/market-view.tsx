"use client";

import { MarketSignalView } from "../../../../components/dashboard-views";
import { MarketChartAutoRefresh } from "../../../../components/market-chart-auto-refresh";
import { MarketCandlestickChartIsland } from "../../../../components/market-candlestick-chart-island";

import type { DashboardViewModuleProps } from "../dashboard-view-types";

export function MarketView({
  sections,
  operatorPayload,
  market,
}: DashboardViewModuleProps) {
  if (!operatorPayload || !market) {
    return null;
  }

  return (
    <MarketSignalView
      operator={operatorPayload}
      snapshots={sections[0]?.rows ?? []}
      features={sections[1]?.rows ?? []}
      chartSnapshots={market.chartFallbackSnapshots}
      chartFeatures={market.chartFallbackFeatures}
      selectedSymbol={market.selectedSymbol}
      selectedCandleWindow={market.selectedCandleWindow}
      selectedTimeframe={market.selectedTimeframe}
      selectedChartZoomRange={market.selectedChartZoomRange}
      chartCandlesBySymbol={market.chartCandlesBySymbol}
      renderAutoRefresh={(props) => <MarketChartAutoRefresh {...props} />}
      renderCandlestickChart={(model) => <MarketCandlestickChartIsland model={model} />}
    />
  );
}
