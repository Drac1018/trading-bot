import type { ReactNode } from "react";
import { notFound } from "next/navigation";

import {
  AgentDebugView,
  DecisionView,
  MarketSignalView,
  PositionsView,
  RiskView,
  SchedulerView,
  type MarketChartZoomRange,
  type MarketChartTimeframe,
} from "../../../components/dashboard-views";
import { DataTable } from "../../../components/data-table";
import { LogExplorer, type AuditRow } from "../../../components/log-explorer";
import { PageShell } from "../../../components/page-shell";
import { SettingsControls, type SettingsPayload } from "../../../components/settings-controls";
import { type OperatorDashboardPayload } from "../../../components/overview-dashboard";
import { fetchJson } from "../../../lib/api";
import { fetchBinanceChartCandlesBySymbol } from "../../../lib/binance-chart-candles";
import { normalizeSettingsView } from "../../../lib/page-config";
import { ALL_SYMBOLS, resolveSelectedSymbol } from "../../../lib/selected-symbol";
import { dashboardPages } from "../../../lib/page-config";

type Row = Record<string, unknown>;
type CandleWindow = 30 | 60 | 120;

function queryValue(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

function resolveCandleWindow(value: string | string[] | undefined): CandleWindow {
  const normalized = queryValue(value);
  if (normalized === "30") {
    return 30;
  }
  if (normalized === "60") {
    return 60;
  }
  if (normalized === "120") {
    return 120;
  }
  return 120;
}

function resolveMarketTimeframe(value: string | string[] | undefined): MarketChartTimeframe {
  const normalized = queryValue(value);
  if (normalized === "1h" || normalized === "60m") {
    return "1h";
  }
  if (normalized === "4h" || normalized === "240m") {
    return "4h";
  }
  return "15m";
}

function resolveMarketChartZoomRange(value: string | string[] | undefined): MarketChartZoomRange | null {
  const normalized = queryValue(value);
  const match = normalized?.match(/^(\d+)-(\d+)$/);
  if (!match) {
    return null;
  }
  const start = Number(match[1]);
  const end = Number(match[2]);
  if (!Number.isInteger(start) || !Number.isInteger(end) || end <= start) {
    return null;
  }
  return { start, end };
}

function resolveDecisionEntryFlowTab(value: string | string[] | undefined): "summary" | "plan" | "execution" {
  const normalized = queryValue(value);
  if (normalized === "plan" || normalized === "execution") {
    return normalized;
  }
  return "summary";
}

function operatorDashboardEndpoint(slug: string) {
  if (slug === "market" || slug === "scheduler") {
    return `/api/dashboard/operator?view=${slug}`;
  }
  return "/api/dashboard/operator";
}

export default async function DashboardPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { slug } = await params;
  const config = dashboardPages[slug];
  const query = await searchParams;

  if (!config) {
    notFound();
  }

  // Audit uses the shared explorer component with audit-only data.
  if (slug === "audit") {
    const auditRows = await fetchJson<AuditRow[]>("/api/audit?limit=30");
    const initialTab = typeof query.tab === "string" ? query.tab : "all";

    return (
      <div className="space-y-6">
        <PageShell eyebrow={config.eyebrow} title={config.title} description={config.description} compact />
        <LogExplorer initialRows={auditRows} initialTab={initialTab} initialLimit={30} />
      </div>
    );
  }

  const settingsPayloadPromise = slug === "settings"
    ? fetchJson<SettingsPayload>("/api/settings")
    : Promise.resolve<SettingsPayload | null>(null);
  const operatorPayloadPromise =
    slug === "market" || slug === "decisions" || slug === "scheduler" || slug === "risk"
      ? fetchJson<OperatorDashboardPayload>(operatorDashboardEndpoint(slug))
      : Promise.resolve<OperatorDashboardPayload | null>(null);

  const sectionsPromise =
    slug === "settings"
      ? Promise.resolve([])
      : Promise.all(
          config.sections.map(async (section) => ({
            ...section,
            rows: await fetchJson<Row[] | Row>(section.endpoint),
          })),
        );
  const [settingsPayload, operatorPayload, sections] = await Promise.all([
    settingsPayloadPromise,
    operatorPayloadPromise,
    sectionsPromise,
  ]);

  const normalizedSections = sections.map((section) => ({
    ...section,
    rows: Array.isArray(section.rows) ? section.rows : [section.rows],
  }));

  let content: ReactNode = null;

  if (slug === "market" && operatorPayload) {
    const requestedMarketSymbol = queryValue(query.symbol);
    const selectedSymbol =
      requestedMarketSymbol?.trim().toUpperCase() === ALL_SYMBOLS
        ? ALL_SYMBOLS
        : resolveSelectedSymbol(
            requestedMarketSymbol,
            operatorPayload.control.tracked_symbols,
            operatorPayload.control.default_symbol,
            { mode: "single" },
          );
    const selectedCandleWindow = resolveCandleWindow(query.candles);
    const selectedTimeframe = resolveMarketTimeframe(query.timeframe);
    const selectedChartZoomRange = resolveMarketChartZoomRange(query.zoom);
    const chartSymbols = selectedSymbol === ALL_SYMBOLS ? [] : [selectedSymbol];
    const chartCandlesBySymbol = await fetchBinanceChartCandlesBySymbol({
      symbols: chartSymbols,
      timeframe: selectedTimeframe,
      limit: selectedCandleWindow,
    });
    const selectedChartCandles =
      selectedSymbol === ALL_SYMBOLS ? [] : (chartCandlesBySymbol[selectedSymbol.toUpperCase()] ?? []);
    const needsChartPayloadFallback = selectedSymbol !== ALL_SYMBOLS && selectedChartCandles.length === 0;
    const [chartFallbackSnapshots, chartFallbackFeatures] = needsChartPayloadFallback
      ? await Promise.all([
          fetchJson<Row[]>(
            `/api/market/snapshots?limit=1&symbol=${encodeURIComponent(selectedSymbol)}&timeframe=${encodeURIComponent(
              selectedTimeframe,
            )}`,
          ),
          fetchJson<Row[]>(
            `/api/market/features?limit=1&symbol=${encodeURIComponent(selectedSymbol)}&timeframe=${encodeURIComponent(
              selectedTimeframe,
            )}`,
          ),
        ])
      : [undefined, undefined];
    content = (
      <MarketSignalView
        operator={operatorPayload}
        snapshots={normalizedSections[0]?.rows ?? []}
        features={normalizedSections[1]?.rows ?? []}
        chartSnapshots={chartFallbackSnapshots}
        chartFeatures={chartFallbackFeatures}
        selectedSymbol={selectedSymbol}
        selectedCandleWindow={selectedCandleWindow}
        selectedTimeframe={selectedTimeframe}
        selectedChartZoomRange={selectedChartZoomRange}
        chartCandlesBySymbol={chartCandlesBySymbol}
      />
    );
  } else if (slug === "decisions" && operatorPayload) {
    const selectedSymbol = resolveSelectedSymbol(
      queryValue(query.symbol),
      operatorPayload.control.tracked_symbols,
      operatorPayload.control.default_symbol,
      { mode: "single" },
    );
    content = (
      <DecisionView
        operator={operatorPayload}
        decisionRows={normalizedSections[0]?.rows ?? []}
        selectedSymbol={selectedSymbol}
        entryFlowTab={resolveDecisionEntryFlowTab(query.flow)}
      />
    );
  } else if (slug === "scheduler" && operatorPayload) {
    content = <SchedulerView operator={operatorPayload} schedulerRows={normalizedSections[0]?.rows ?? []} />;
  } else if (slug === "positions") {
    content = <PositionsView positionRows={normalizedSections[0]?.rows ?? []} />;
  } else if (slug === "risk") {
    content = (
      <RiskView
        operator={operatorPayload}
        riskRows={normalizedSections[0]?.rows ?? []}
        alertRows={normalizedSections[1]?.rows ?? []}
      />
    );
  } else if (slug === "agents") {
    content = <AgentDebugView agentRows={normalizedSections[0]?.rows ?? []} />;
  } else if (slug === "settings") {
    content = settingsPayload ? (
      <SettingsControls
        initial={settingsPayload}
        initialView={normalizeSettingsView(queryValue(query.view))}
      />
    ) : null;
  } else {
    content = normalizedSections.map((section) => (
      <DataTable
        key={section.title}
        title={section.title}
        description={section.description}
        rows={section.rows}
      />
    ));
  }

  return (
    <div className="space-y-6">
      <PageShell eyebrow={config.eyebrow} title={config.title} description={config.description} compact />

      {content}
    </div>
  );
}
