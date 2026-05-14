import { notFound, redirect } from "next/navigation";

import { DashboardViewLoader } from "./dashboard-view-loader";
import type {
  CandleWindow,
  MarketChartTimeframe,
  MarketChartZoomRange,
  MarketDashboardViewData,
  NormalizedDashboardSection,
  Row,
} from "./dashboard-view-types";
import type { SettingsPayload } from "../../../components/settings-controls";
import { PageShell } from "../../../components/page-shell";
import type { OperatorDashboardPayload } from "../../../components/overview-dashboard";
import { fetchJson } from "../../../lib/api";
import { fetchBinanceChartCandlesBySymbol } from "../../../lib/binance-chart-candles";
import { dashboardPages } from "../../../lib/page-config";
import { ordersDataEndpoints } from "../../../lib/orders-query";
import { ALL_SYMBOLS, resolveSelectedSymbol } from "../../../lib/selected-symbol";

function queryValue(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

function appendQueryParams(
  path: string,
  query: Record<string, string | string[] | undefined>,
  overrides: Record<string, string>,
  rename: Record<string, string> = {},
) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || overrides[key]) {
      continue;
    }
    const nextKey = rename[key] ?? key;
    if (Array.isArray(value)) {
      for (const item of value) {
        params.append(nextKey, item);
      }
      continue;
    }
    params.set(nextKey, value);
  }

  for (const [key, value] of Object.entries(overrides)) {
    params.set(key, value);
  }

  return `${path}?${params.toString()}`;
}

function legacyDashboardRedirect(
  slug: string,
  query: Record<string, string | string[] | undefined>,
) {
  if (slug === "decisions" || slug === "risk" || slug === "scheduler") {
    return appendQueryParams("/dashboard/operations", query, { section: slug });
  }
  if (slug === "positions") {
    return appendQueryParams("/dashboard/trading", query, { section: "positions" });
  }
  if (slug === "orders") {
    return appendQueryParams(
      "/dashboard/trading",
      query,
      { section: "orders" },
      { tab: "ordersTab" },
    );
  }
  if (slug === "agents") {
    return appendQueryParams("/dashboard/audit", query, { section: "agents" });
  }
  return null;
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
  const query = await searchParams;
  const legacyRedirectPath = legacyDashboardRedirect(slug, query);

  if (legacyRedirectPath) {
    redirect(legacyRedirectPath);
  }

  const config = dashboardPages[slug];

  if (!config) {
    notFound();
  }

  const settingsPayloadPromise =
    slug === "settings"
      ? fetchJson<SettingsPayload>("/api/settings")
      : Promise.resolve<SettingsPayload | null>(null);
  const operatorPayloadPromise =
    slug === "market" || slug === "decisions" || slug === "scheduler" || slug === "risk"
      ? fetchJson<OperatorDashboardPayload>(operatorDashboardEndpoint(slug))
      : Promise.resolve<OperatorDashboardPayload | null>(null);
  const ordersEndpoints = slug === "orders" ? ordersDataEndpoints(query) : null;
  const sectionsPromise =
    slug === "settings" || slug === "market"
      ? Promise.resolve([])
      : Promise.all(
          config.sections.map(async (section, index) => ({
            ...section,
            rows: await fetchJson<Row[] | Row>(ordersEndpoints?.[index] ?? section.endpoint),
          })),
        );

  const [settingsPayload, operatorPayload, sections] = await Promise.all([
    settingsPayloadPromise,
    operatorPayloadPromise,
    sectionsPromise,
  ]);

  const normalizedSections: NormalizedDashboardSection[] = sections.map((section) => ({
    ...section,
    rows: Array.isArray(section.rows) ? section.rows : [section.rows],
  }));

  let market: MarketDashboardViewData | null = null;
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
    const featurePayloadTimeframe = selectedTimeframe === "15m" ? selectedTimeframe : "15m";
    const encodedSelectedSymbol = encodeURIComponent(selectedSymbol);
    const chartSourceRows =
      selectedSymbol === ALL_SYMBOLS
        ? { snapshots: undefined, features: undefined }
        : await (async () => {
            const compactSnapshotEndpoint = `/api/market/snapshots?limit=1&compact=true&symbol=${encodedSelectedSymbol}&timeframe=15m`;
            const featureEndpoint = `/api/market/features?limit=1&symbol=${encodedSelectedSymbol}&timeframe=${encodeURIComponent(
              featurePayloadTimeframe,
            )}`;
            const fallbackSnapshotEndpoint = `/api/market/snapshots?limit=1&symbol=${encodedSelectedSymbol}&timeframe=15m`;
            const [compactSnapshots, features, fallbackSnapshots] = await Promise.all([
              fetchJson<Row[]>(compactSnapshotEndpoint),
              fetchJson<Row[]>(featureEndpoint),
              needsChartPayloadFallback ? fetchJson<Row[]>(fallbackSnapshotEndpoint) : Promise.resolve(undefined),
            ]);
            return {
              snapshots: needsChartPayloadFallback && fallbackSnapshots ? fallbackSnapshots : compactSnapshots,
              features,
            };
          })();
    market = {
      selectedSymbol,
      selectedCandleWindow,
      selectedTimeframe,
      selectedChartZoomRange,
      chartCandlesBySymbol,
      chartMarkers: [],
      chartFallbackSnapshots: chartSourceRows.snapshots,
      chartFallbackFeatures: chartSourceRows.features,
    };
  }

  const content = (
    <DashboardViewLoader
      slug={slug}
      query={query}
      sections={normalizedSections}
      operatorPayload={operatorPayload}
      settingsPayload={settingsPayload}
      market={market}
    />
  );

  return (
    <div className="space-y-6">
      <PageShell eyebrow={config.eyebrow} title={config.title} description={config.description} compact />

      {content}
    </div>
  );
}
