import type { OperatorDashboardPayload } from "../../../components/overview-dashboard";
import type { SettingsPayload } from "../../../components/settings-controls";
import type { BinanceChartCandle } from "../../../lib/binance-chart-candles";
import type { PageSection } from "../../../lib/page-config";

export type Row = Record<string, unknown>;
export type CandleWindow = 30 | 60 | 120;
export type MarketChartTimeframe = "15m" | "1h" | "4h";
export type MarketChartZoomRange = { start: number; end: number };
export type DecisionEntryFlowTab = "summary" | "plan" | "execution";
export type OrderLifecycleTab = "summary" | "orders" | "executions";

export type NormalizedDashboardSection = PageSection & {
  rows: Row[];
};

export type MarketDashboardViewData = {
  selectedSymbol: string;
  selectedCandleWindow: CandleWindow;
  selectedTimeframe: MarketChartTimeframe;
  selectedChartZoomRange: MarketChartZoomRange | null;
  chartCandlesBySymbol: Record<string, BinanceChartCandle[]>;
  chartFallbackSnapshots?: Row[];
  chartFallbackFeatures?: Row[];
};

export type DashboardViewModuleProps = {
  query: Record<string, string | string[] | undefined>;
  sections: NormalizedDashboardSection[];
  operatorPayload: OperatorDashboardPayload | null;
  settingsPayload: SettingsPayload | null;
  market: MarketDashboardViewData | null;
};
