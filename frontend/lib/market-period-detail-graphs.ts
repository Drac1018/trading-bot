import type { MarketChartEventMarker } from "./market-chart-markers";

export type MarketPeriodPricePoint = {
  timestamp: string;
  close: number | null | undefined;
};

export type MarketPriceTrendStats = {
  count: number;
  minPrice: number;
  maxPrice: number;
  firstTimestamp: string;
  lastTimestamp: string;
};

export type MarketBlockedReasonDistributionItem = {
  key: string;
  label: string;
  count: number;
  codes: string[];
};

type MarketBlockedReasonDistributionInput = {
  symbol: string;
  candles: readonly MarketPeriodPricePoint[];
  markers: readonly MarketChartEventMarker[];
  reasonLabeler: (code: string) => string;
};

function timestampMs(value: string | null | undefined): number | null {
  if (!value) {
    return null;
  }
  const parsed = Date.parse(value.endsWith("Z") ? value : `${value}Z`);
  return Number.isNaN(parsed) ? null : parsed;
}

function candleTimeRange(candles: readonly MarketPeriodPricePoint[]): { start: number; end: number } | null {
  const times = candles
    .map((candle) => timestampMs(candle.timestamp))
    .filter((value): value is number => value !== null);
  if (times.length === 0) {
    return null;
  }
  return {
    start: Math.min(...times),
    end: Math.max(...times),
  };
}

export function marketPriceTrendStats(candles: readonly MarketPeriodPricePoint[]): MarketPriceTrendStats | null {
  const points = candles.filter(
    (candle): candle is { timestamp: string; close: number } =>
      typeof candle.close === "number" && Number.isFinite(candle.close) && timestampMs(candle.timestamp) !== null,
  );
  if (points.length === 0) {
    return null;
  }
  return {
    count: points.length,
    minPrice: Math.min(...points.map((point) => point.close)),
    maxPrice: Math.max(...points.map((point) => point.close)),
    firstTimestamp: points[0]?.timestamp ?? "",
    lastTimestamp: points[points.length - 1]?.timestamp ?? "",
  };
}

export function buildMarketBlockedReasonDistribution({
  symbol,
  candles,
  markers,
  reasonLabeler,
}: MarketBlockedReasonDistributionInput): MarketBlockedReasonDistributionItem[] {
  const range = candleTimeRange(candles);
  if (!range) {
    return [];
  }

  const counts = new Map<string, MarketBlockedReasonDistributionItem>();
  const normalizedSymbol = symbol.toUpperCase();

  for (const marker of markers) {
    if (marker.symbol.toUpperCase() !== normalizedSymbol || marker.kind !== "risk_blocked") {
      continue;
    }
    const createdAtMs = timestampMs(marker.timestamp);
    if (createdAtMs === null || createdAtMs < range.start || createdAtMs > range.end) {
      continue;
    }
    const reasonCodes =
      marker.reasonCodes && marker.reasonCodes.length > 0
        ? marker.reasonCodes
        : marker.reasonLabel
          ? [marker.reasonLabel]
          : [];
    for (const code of reasonCodes) {
      const label = reasonLabeler(code);
      const existing = counts.get(label);
      if (existing) {
        existing.count += 1;
        if (!existing.codes.includes(code)) {
          existing.codes.push(code);
        }
        continue;
      }
      counts.set(label, {
        key: label,
        label,
        count: 1,
        codes: [code],
      });
    }
  }

  return [...counts.values()].sort((left, right) => right.count - left.count || left.label.localeCompare(right.label, "ko"));
}
