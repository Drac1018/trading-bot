export type MarketChartTimeframeValue = "15m" | "1h" | "4h";

export type MarketTimeframeAvailability = Map<
  MarketChartTimeframeValue,
  { enabled: boolean; detail: string }
>;

export type MarketTimeframeRow = Record<string, unknown>;

export const marketChartTimeframeValues: MarketChartTimeframeValue[] = ["15m", "1h", "4h"];

function rowString(row: MarketTimeframeRow, key: string) {
  const value = row[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function asRecord(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function stringArray(value: unknown) {
  return Array.isArray(value)
    ? value.map((item) => (typeof item === "string" ? item.trim() : "")).filter(Boolean)
    : [];
}

function metadataTimeframes(feature: MarketTimeframeRow) {
  const preferred =
    stringArray(feature.available_timeframes).length > 0
      ? stringArray(feature.available_timeframes)
      : stringArray(feature.supported_timeframes);
  if (preferred.length > 0) {
    return preferred;
  }
  return stringArray(feature.multi_timeframe_keys);
}

function featureSupportedTimeframes(feature: MarketTimeframeRow) {
  const supported = new Set<string>();
  const direct = rowString(feature, "timeframe");
  if (direct) {
    supported.add(direct);
  }

  const metadata = metadataTimeframes(feature);
  if (metadata.length > 0) {
    metadata.forEach((timeframe) => supported.add(timeframe));
    return supported;
  }

  const payload = asRecord(feature.payload);
  const multiTimeframe = asRecord(payload?.multi_timeframe);
  Object.keys(multiTimeframe ?? {}).forEach((timeframe) => supported.add(timeframe));
  return supported;
}

function timeframeDetail({
  timeframe,
  enabled,
  directSnapshot,
  hasBase,
  directFeatureTimeframes,
}: {
  timeframe: MarketChartTimeframeValue;
  enabled: boolean;
  directSnapshot: boolean;
  hasBase: boolean;
  directFeatureTimeframes: Set<string>;
}) {
  if (timeframe === "15m") {
    return enabled ? "15m 시장 스냅샷/feature 기준" : "15m 기준 데이터 없음";
  }
  if (enabled && directSnapshot && directFeatureTimeframes.has(timeframe)) {
    return `${timeframe} 직접 스냅샷/feature row 기준`;
  }
  if (enabled) {
    return `15m 기준 feature의 ${timeframe} multi_timeframe 컨텍스트`;
  }
  if (!hasBase) {
    return "15m 기준 스냅샷 또는 feature 없음";
  }
  return `15m 기준 feature metadata에 ${timeframe} 컨텍스트 없음`;
}

export function resolveAvailableMarketTimeframes(
  snapshots: MarketTimeframeRow[],
  features: MarketTimeframeRow[],
): MarketTimeframeAvailability {
  const snapshotTimeframes = new Set(snapshots.map((row) => rowString(row, "timeframe")).filter(Boolean));
  const featureTimeframes = new Set<string>();
  const directFeatureTimeframes = new Set<string>();
  let hasBaseFeature = false;

  for (const feature of features) {
    const direct = rowString(feature, "timeframe");
    if (direct === "15m") {
      hasBaseFeature = true;
    }
    if (direct) {
      directFeatureTimeframes.add(direct);
    }
    for (const timeframe of featureSupportedTimeframes(feature)) {
      featureTimeframes.add(timeframe);
    }
  }

  const hasBase = snapshotTimeframes.has("15m") || hasBaseFeature;

  return new Map(
    marketChartTimeframeValues.map((timeframe) => {
      const directSnapshot = snapshotTimeframes.has(timeframe);
      const featureSupported = featureTimeframes.has(timeframe);
      const enabled =
        timeframe === "15m"
          ? directSnapshot || featureSupported
          : (hasBase && featureSupported) || (directSnapshot && featureSupported);
      return [
        timeframe,
        {
          enabled,
          detail: timeframeDetail({
            timeframe,
            enabled,
            directSnapshot,
            hasBase,
            directFeatureTimeframes,
          }),
        },
      ] as const;
    }),
  );
}

export function resolveEffectiveMarketTimeframe(
  requested: MarketChartTimeframeValue,
  availability: MarketTimeframeAvailability,
) {
  return availability.get(requested)?.enabled ? requested : "15m";
}
