"use client";

import type { MarketChartEventMarker as ClientMarketChartEventMarker } from "../lib/market-chart-markers";
import { MarketChartZoomShell } from "./market-chart-zoom-shell";

export type ClientMarketChartTimeframe = "15m" | "1h" | "4h";

export type ClientCandlePoint = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type ClientMarketChartStats = {
  firstClose: number | null;
  lastClose: number | null;
  high: number | null;
  low: number | null;
  changePct: number | null;
  rangePct: number | null;
  rangePositionPct: number | null;
  averageVolume: number | null;
  volumeVsAverage: number | null;
};

export type ClientMarketCandlestickModel = {
  symbol: string;
  timeframe: ClientMarketChartTimeframe;
  latestPrice: number | null;
  candles: ClientCandlePoint[];
  baseCandleCount: number;
  visibleStartIndex: number;
  visibleEndIndex: number;
  stats: ClientMarketChartStats;
  backendVolumeProfile: Record<string, unknown> | null;
  aiTooltipRows: string[];
  volumeProfileTooltipRows: string[];
  events: ClientMarketChartEventMarker[];
};

type MarketOverlayKey = "close" | "volume" | "levels" | "averageVolume";
type CandleIndicatorPoint = {
  rsi: number | null;
  atr: number | null;
  atrPct: number | null;
  volatilityPct: number | null;
};
type BollingerPoint = {
  upper: number | null;
  middle: number | null;
  lower: number | null;
  bandwidthPct: number | null;
};
type VolumeProfileBin = {
  low: number;
  high: number;
  mid: number;
  volume: number;
  strength: number;
};

const defaultMarketOverlays: MarketOverlayKey[] = ["close", "volume", "levels", "averageVolume"];

function asFiniteNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value.endsWith("Z") ? value : `${value}Z`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function formatNumber(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined) {
    return "-";
  }
  return value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatSignedNumber(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined) {
    return "-";
  }
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${formatNumber(value, digits)}`;
}

function formatPercentPoint(value: number | null | undefined, digits = 2) {
  return value === null || value === undefined ? "-" : `${formatSignedNumber(value, digits)}%`;
}

function formatUnsignedPercent(value: number | null | undefined, digits = 2) {
  return value === null || value === undefined ? "-" : `${formatNumber(value, digits)}%`;
}

function marketPriceDigits(value: number | null | undefined) {
  if (value === null || value === undefined || value >= 100) {
    return 2;
  }
  return value >= 1 ? 4 : 5;
}

function marketTone(value: number | null | undefined): "good" | "warn" | "danger" | "neutral" {
  if (value === null || value === undefined) {
    return "neutral";
  }
  if (value > 0.15) {
    return "good";
  }
  if (value < -0.15) {
    return "danger";
  }
  return "neutral";
}

function buildCandleIndicatorSeries(candles: ClientCandlePoint[], period = 14): CandleIndicatorPoint[] {
  return candles.map((candle, index) => {
    const start = Math.max(0, index - period + 1);
    const window = candles.slice(start, index + 1);
    const volatilityPct = candle.close !== 0 ? ((candle.high - candle.low) / candle.close) * 100 : null;
    const previous = candles[index - 1];
    const trueRange = previous
      ? Math.max(candle.high - candle.low, Math.abs(candle.high - previous.close), Math.abs(candle.low - previous.close))
      : candle.high - candle.low;
    const atr =
      window.length < 2
        ? trueRange
        : window.reduce((total, item, windowIndex) => {
            const previousWindowCandle = candles[start + windowIndex - 1];
            const itemTrueRange = previousWindowCandle
              ? Math.max(item.high - item.low, Math.abs(item.high - previousWindowCandle.close), Math.abs(item.low - previousWindowCandle.close))
              : item.high - item.low;
            return total + itemTrueRange;
          }, 0) / window.length;
    let gain = 0;
    let loss = 0;
    for (let cursor = start + 1; cursor <= index; cursor += 1) {
      const current = candles[cursor];
      const previousCandle = candles[cursor - 1];
      if (!current || !previousCandle) {
        continue;
      }
      const delta = current.close - previousCandle.close;
      if (delta >= 0) {
        gain += delta;
      } else {
        loss += Math.abs(delta);
      }
    }
    const rsi =
      index === 0
        ? null
        : loss === 0
          ? 100
          : 100 - 100 / (1 + gain / Math.max(loss, 0.0000001));
    return {
      rsi,
      atr,
      atrPct: candle.close !== 0 ? (atr / candle.close) * 100 : null,
      volatilityPct,
    };
  });
}

function buildBollingerSeries(candles: ClientCandlePoint[], period = 20, multiplier = 2): BollingerPoint[] {
  return candles.map((_candle, index) => {
    if (index + 1 < period) {
      return {
        upper: null,
        middle: null,
        lower: null,
        bandwidthPct: null,
      };
    }
    const window = candles.slice(index - period + 1, index + 1);
    const middle = window.reduce((total, item) => total + item.close, 0) / window.length;
    const variance = window.reduce((total, item) => total + (item.close - middle) ** 2, 0) / window.length;
    const deviation = Math.sqrt(variance);
    const upper = middle + deviation * multiplier;
    const lower = middle - deviation * multiplier;
    return {
      upper,
      middle,
      lower,
      bandwidthPct: middle !== 0 ? ((upper - lower) / middle) * 100 : null,
    };
  });
}

function buildVolumeProfile(candles: ClientCandlePoint[], binCount = 28): VolumeProfileBin[] {
  if (candles.length === 0) {
    return [];
  }
  const low = Math.min(...candles.map((item) => item.low));
  const high = Math.max(...candles.map((item) => item.high));
  const range = Math.max(high - low, 0.0000001);
  const bins = Array.from({ length: binCount }, (_, index) => {
    const binLow = low + (range / binCount) * index;
    const binHigh = low + (range / binCount) * (index + 1);
    return {
      low: binLow,
      high: binHigh,
      mid: (binLow + binHigh) / 2,
      volume: 0,
      strength: 0,
    };
  });
  for (const candle of candles) {
    const typicalPrice = (candle.high + candle.low + candle.close) / 3;
    const index = Math.max(0, Math.min(binCount - 1, Math.floor(((typicalPrice - low) / range) * binCount)));
    const bin = bins[index];
    if (bin) {
      bin.volume += candle.volume;
    }
  }
  const maxVolume = Math.max(...bins.map((item) => item.volume), 1);
  return bins.map((item) => ({
    ...item,
    strength: item.volume / maxVolume,
  }));
}

function profileBinForPrice(profile: VolumeProfileBin[], price: number) {
  return (
    profile.find((item) => price >= item.low && price <= item.high) ??
    profile.reduce<VolumeProfileBin | null>((nearest, item) => {
      if (nearest === null) {
        return item;
      }
      return Math.abs(item.mid - price) < Math.abs(nearest.mid - price) ? item : nearest;
    }, null)
  );
}

function profilePointRows(bin: VolumeProfileBin | null, profile: VolumeProfileBin[], priceDigits: number) {
  if (!bin) {
    return [];
  }
  const poc = profile.reduce<VolumeProfileBin | null>((strongest, item) => {
    if (strongest === null) {
      return item;
    }
    return item.volume > strongest.volume ? item : strongest;
  }, null);
  return [
    `매물대: ${formatNumber(bin.low, priceDigits)}~${formatNumber(bin.high, priceDigits)} / ${formatNumber(bin.strength * 100, 0)}%`,
    `최대 거래 매물대: ${formatNumber(poc?.mid, priceDigits)}`,
  ];
}

function candleTooltipRows(
  candle: ClientCandlePoint,
  priceDigits: number,
  indicator: CandleIndicatorPoint | null = null,
  extras: {
    bollinger?: BollingerPoint | null;
    profileRows?: string[];
    markerRows?: string[];
    aiRows?: string[];
  } = {},
) {
  const changePct = candle.open !== 0 ? ((candle.close - candle.open) / candle.open) * 100 : null;
  return [
    `시각: ${formatDateTime(candle.timestamp)}`,
    `시가: ${formatNumber(candle.open, priceDigits)}`,
    `고가: ${formatNumber(candle.high, priceDigits)}`,
    `저가: ${formatNumber(candle.low, priceDigits)}`,
    `종가: ${formatNumber(candle.close, priceDigits)}`,
    `봉 변화: ${formatPercentPoint(changePct, 2)}`,
    `거래량: ${formatNumber(candle.volume, 2)}`,
    `RSI: ${formatNumber(indicator?.rsi, 1)}`,
    `ATR: ${formatNumber(indicator?.atr, priceDigits)} / ${formatUnsignedPercent(indicator?.atrPct, 2)}`,
    `변동폭: ${formatUnsignedPercent(indicator?.volatilityPct, 2)}`,
    ...(extras.bollinger?.middle !== null && extras.bollinger?.middle !== undefined
      ? [
          `볼린저밴드: ${formatNumber(extras.bollinger.lower, priceDigits)} / ${formatNumber(
            extras.bollinger.middle,
            priceDigits,
          )} / ${formatNumber(extras.bollinger.upper, priceDigits)}`,
        ]
      : []),
    ...(extras.profileRows ?? []),
    ...(extras.markerRows ?? []),
    ...(extras.aiRows ?? []),
  ];
}

function timestampMs(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const parsed = Date.parse(value.endsWith("Z") ? value : `${value}Z`);
  return Number.isNaN(parsed) ? null : parsed;
}

function marketEventMarkerTooltipLabel(kind: ClientMarketChartEventMarker["kind"]) {
  if (kind === "risk_approved") {
    return "리스크 승인";
  }
  if (kind === "risk_blocked") {
    return "리스크 차단";
  }
  if (kind === "execution") {
    return "실제 실행";
  }
  return "AI 추천";
}

function marketEventMarkerTooltipRows(marker: ClientMarketChartEventMarker, priceDigits: number) {
  const rows = [
    `시각: ${formatDateTime(marker.timestamp)}`,
    `심볼: ${marker.symbol}`,
    `판단/액션: ${marker.action || marker.detail}`,
    `가격: ${formatNumber(marker.price, priceDigits)}`,
    `상태: ${marker.statusLabel || marketEventMarkerTooltipLabel(marker.kind)}`,
  ];
  if (marker.reasonLabel) {
    rows.push(`${marker.kind === "risk_blocked" ? "차단 사유" : "사유"}: ${marker.reasonLabel}`);
  }
  return rows;
}

function clampSvgY(value: number, top: number, bottom: number) {
  return Math.max(top, Math.min(bottom, value));
}

export function MarketCandlestickChartClient({
  model,
  compact = false,
}: {
  model: ClientMarketCandlestickModel;
  compact?: boolean;
}) {
  const candles = model.candles;
  if (candles.length === 0) {
    return (
      <div className="flex min-h-56 items-center justify-center rounded-md border border-dashed border-slate-300 bg-white text-sm text-slate-500">
        {model.timeframe} 캔들 payload가 없습니다.
      </div>
    );
  }

  const width = 860;
  const showIndicatorPanels = !compact;
  const height = compact ? 188 : 548;
  const left = 50;
  const right = 20;
  const priceTop = compact ? 24 : 28;
  const priceHeight = compact ? 96 : 226;
  const volumeTop = compact ? 138 : 284;
  const volumeHeight = compact ? 26 : 48;
  const indicatorHeight = 42;
  const indicatorGap = 18;
  const rsiTop = volumeTop + volumeHeight + 28;
  const atrTop = rsiTop + indicatorHeight + indicatorGap;
  const volatilityTop = atrTop + indicatorHeight + indicatorGap;
  const indicatorBottom = showIndicatorPanels ? volatilityTop + indicatorHeight : volumeTop + volumeHeight;
  const plotWidth = width - left - right;
  const selectedOverlays = defaultMarketOverlays;
  const showCloseLine = selectedOverlays.includes("close");
  const showVolume = selectedOverlays.includes("volume");
  const showLevels = selectedOverlays.includes("levels");
  const showAverageVolume = showVolume && selectedOverlays.includes("averageVolume");
  const indicatorSeries = buildCandleIndicatorSeries(candles);
  const bollingerSeries = buildBollingerSeries(candles);
  const backendVolumeProfile = model.backendVolumeProfile;
  const backendPocPrice = asFiniteNumber(backendVolumeProfile?.poc_price);
  const backendValueAreaLow = asFiniteNumber(backendVolumeProfile?.value_area_low);
  const backendValueAreaHigh = asFiniteNumber(backendVolumeProfile?.value_area_high);
  const bollingerPriceValues = bollingerSeries.flatMap((item) => [item.upper, item.lower]).filter((value): value is number => value !== null);
  const backendProfilePriceValues = [backendPocPrice, backendValueAreaLow, backendValueAreaHigh].filter(
    (value): value is number => value !== null,
  );
  const minPrice = Math.min(...candles.map((item) => item.low), ...bollingerPriceValues, ...backendProfilePriceValues);
  const maxPrice = Math.max(...candles.map((item) => item.high), ...bollingerPriceValues, ...backendProfilePriceValues);
  const volumeProfile = buildVolumeProfile(candles);
  const maxVolume = Math.max(...candles.map((item) => item.volume), 1);
  const priceRange = Math.max(maxPrice - minPrice, 1);
  const step = candles.length > 1 ? plotWidth / (candles.length - 1) : plotWidth;
  const candleWidth = Math.max(0.8, Math.min(9, step * 0.58));
  const priceDigits = marketPriceDigits(model.latestPrice ?? candles[candles.length - 1]?.close);
  const xFor = (index: number) => left + index * step;
  const yForPrice = (value: number) => priceTop + ((maxPrice - value) / priceRange) * priceHeight;
  const yForVolume = (value: number) => volumeTop + volumeHeight - (value / maxVolume) * volumeHeight;
  const toLinePoints = (points: (string | null)[]) => points.filter((point): point is string => point !== null).join(" ");
  const rsiLine = showIndicatorPanels
    ? toLinePoints(
        indicatorSeries.map((item, index) =>
          item.rsi === null
            ? null
            : `${xFor(index).toFixed(2)},${(rsiTop + ((100 - item.rsi) / 100) * indicatorHeight).toFixed(2)}`,
        ),
      )
    : "";
  const atrPctValues = indicatorSeries
    .map((item) => item.atrPct)
    .filter((value): value is number => value !== null && Number.isFinite(value));
  const maxAtrPct = Math.max(...atrPctValues, 0.01);
  const atrLine = showIndicatorPanels
    ? toLinePoints(
        indicatorSeries.map((item, index) =>
          item.atrPct === null
            ? null
            : `${xFor(index).toFixed(2)},${(atrTop + indicatorHeight - (item.atrPct / maxAtrPct) * indicatorHeight).toFixed(2)}`,
        ),
      )
    : "";
  const volatilityValues = indicatorSeries.map((item) => item.volatilityPct ?? 0);
  const maxVolatilityPct = Math.max(...volatilityValues, 0.01);
  const yForVolatility = (value: number) => volatilityTop + indicatorHeight - (value / maxVolatilityPct) * indicatorHeight;
  const volatilityBarWidth = Math.max(1.4, Math.min(candleWidth, step * 0.52));
  const closeLine = candles.map((item, index) => `${xFor(index).toFixed(2)},${yForPrice(item.close).toFixed(2)}`).join(" ");
  const bollingerUpperLine = toLinePoints(
    bollingerSeries.map((item, index) =>
      item.upper === null ? null : `${xFor(index).toFixed(2)},${yForPrice(item.upper).toFixed(2)}`,
    ),
  );
  const bollingerMiddleLine = toLinePoints(
    bollingerSeries.map((item, index) =>
      item.middle === null ? null : `${xFor(index).toFixed(2)},${yForPrice(item.middle).toFixed(2)}`,
    ),
  );
  const bollingerLowerLine = toLinePoints(
    bollingerSeries.map((item, index) =>
      item.lower === null ? null : `${xFor(index).toFixed(2)},${yForPrice(item.lower).toFixed(2)}`,
    ),
  );
  const bollingerBandArea = (() => {
    const upperPoints = bollingerSeries
      .map((item, index) => (item.upper === null ? null : `${xFor(index).toFixed(2)},${yForPrice(item.upper).toFixed(2)}`))
      .filter((point): point is string => point !== null);
    const lowerPoints = bollingerSeries
      .map((item, index) => (item.lower === null ? null : `${xFor(index).toFixed(2)},${yForPrice(item.lower).toFixed(2)}`))
      .filter((point): point is string => point !== null)
      .reverse();
    return upperPoints.length > 1 && lowerPoints.length > 1 ? [...upperPoints, ...lowerPoints].join(" ") : "";
  })();
  const rectPath = (x: number, y: number, widthValue: number, heightValue: number) =>
    `M ${x.toFixed(2)} ${y.toFixed(2)} h ${widthValue.toFixed(2)} v ${heightValue.toFixed(2)} h -${widthValue.toFixed(2)} Z`;
  const hoverHeight = (showVolume ? indicatorBottom : priceTop + priceHeight) - priceTop;
  const tooltipWidth = compact ? 214 : 250;
  const maxTooltipRows = compact ? 8 : 13;
  const candleCenters = candles.map((_, index) => xFor(index));
  const upWickPath: string[] = [];
  const downWickPath: string[] = [];
  const upBodyPath: string[] = [];
  const downBodyPath: string[] = [];
  const upVolumePath: string[] = [];
  const downVolumePath: string[] = [];
  candles.forEach((item, index) => {
    const x = xFor(index);
    const openY = yForPrice(item.open);
    const closeY = yForPrice(item.close);
    const highY = yForPrice(item.high);
    const lowY = yForPrice(item.low);
    const volumeY = yForVolume(item.volume);
    const up = item.close >= item.open;
    const wickPath = `M ${x.toFixed(2)} ${highY.toFixed(2)} V ${lowY.toFixed(2)}`;
    const bodyPath = rectPath(
      x - candleWidth / 2,
      Math.min(openY, closeY),
      candleWidth,
      Math.max(1.2, Math.abs(openY - closeY)),
    );
    const volumePath = rectPath(
      x - candleWidth / 2,
      volumeY,
      candleWidth,
      Math.max(1, volumeTop + volumeHeight - volumeY),
    );
    if (up) {
      upWickPath.push(wickPath);
      upBodyPath.push(bodyPath);
      upVolumePath.push(volumePath);
    } else {
      downWickPath.push(wickPath);
      downBodyPath.push(bodyPath);
      downVolumePath.push(volumePath);
    }
  });
  const firstCandle = candles[0];
  const lastCandle = candles[candles.length - 1];
  const stats = model.stats;
  const latestClose = stats.lastClose ?? model.latestPrice;
  const latestY = latestClose !== null ? yForPrice(latestClose) : null;
  const latestTone = marketTone(stats.changePct);
  const latestToneColors = {
    good: { fill: "#ecfdf5", stroke: "#a7f3d0" },
    warn: { fill: "#fffbeb", stroke: "#fde68a" },
    danger: { fill: "#fff1f2", stroke: "#fecdd3" },
    neutral: { fill: "#f8fafc", stroke: "#e2e8f0" },
  }[latestTone];
  const markerColors = {
    ai: { fill: "#2563eb", stroke: "#bfdbfe" },
    risk_approved: { fill: "#d97706", stroke: "#fde68a" },
    risk_blocked: { fill: "#e11d48", stroke: "#fecdd3" },
    execution: { fill: "#059669", stroke: "#a7f3d0" },
  };
  const candleTimes = candles.map((item) => timestampMs(item.timestamp) ?? 0);
  const chartStart = candleTimes[0] ?? 0;
  const chartEnd = candleTimes[candleTimes.length - 1] ?? 0;
  const markerSlots = new Map<number, number>();
  const visibleMarkers = model.events.flatMap((marker) => {
    const markerTime = timestampMs(marker.timestamp);
    if (markerTime === null || markerTime < chartStart || markerTime > chartEnd) {
      return [];
    }
    let nearestIndex = 0;
    let nearestDistance = Number.POSITIVE_INFINITY;
    candleTimes.forEach((candleTime, index) => {
      const distance = Math.abs(candleTime - markerTime);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestIndex = index;
      }
    });
    const slot = markerSlots.get(nearestIndex) ?? 0;
    markerSlots.set(nearestIndex, slot + 1);
    return [{ ...marker, index: nearestIndex, x: xFor(nearestIndex), slot }];
  });
  const markerRowsByIndex = new Map<number, string[]>();
  visibleMarkers.forEach((marker) => {
    const rows = markerRowsByIndex.get(marker.index) ?? [];
    rows.push(...marketEventMarkerTooltipRows(marker, priceDigits));
    markerRowsByIndex.set(marker.index, rows);
  });
  const aiRows = model.aiTooltipRows;
  const latestIndex = candles.length - 1;
  const aiMarkerIndexes = new Set(visibleMarkers.filter((marker) => marker.kind === "ai").map((marker) => marker.index));
  const hoverZones = candles.map((item, index) => {
    const center = candleCenters[index] ?? left;
    const previousCenter = candleCenters[index - 1];
    const nextCenter = candleCenters[index + 1];
    const zoneLeft = previousCenter === undefined ? left : (previousCenter + center) / 2;
    const zoneRight = nextCenter === undefined ? width - right : (center + nextCenter) / 2;
    const tooltipX = center + tooltipWidth + 14 > width - right ? center - tooltipWidth - 12 : center + 12;
    const profileRows = profilePointRows(profileBinForPrice(volumeProfile, item.close), volumeProfile, priceDigits);
    const markerRows = markerRowsByIndex.get(index) ?? [];
    const showAiRows = index === latestIndex || aiMarkerIndexes.has(index);
    const tooltipExtras = {
      bollinger: bollingerSeries[index] ?? null,
      profileRows,
      markerRows,
      aiRows: showAiRows ? [...aiRows, ...model.volumeProfileTooltipRows] : [],
    };
    const tooltipRows = candleTooltipRows(item, priceDigits, indicatorSeries[index] ?? null, tooltipExtras);
    const visibleTooltipRows =
      tooltipRows.length > maxTooltipRows
        ? [...tooltipRows.slice(0, maxTooltipRows - 1), `외 ${tooltipRows.length - maxTooltipRows + 1}개 항목`]
        : tooltipRows;
    return {
      item,
      index,
      center,
      x: zoneLeft,
      width: Math.max(1, zoneRight - zoneLeft),
      tooltipX: Math.max(left + 4, Math.min(width - right - tooltipWidth, tooltipX)),
      tooltipY: compact ? 10 : priceTop + 10,
      tooltip: tooltipRows.join("\n"),
      tooltipRows: visibleTooltipRows,
      tooltipHeight: Math.max(44, 22 + visibleTooltipRows.length * 14),
    };
  });
  const latestIndicator = indicatorSeries[indicatorSeries.length - 1] ?? null;

  const chart = (
    <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${model.symbol} ${model.timeframe} 가격, 거래량, RSI, ATR, 변동폭`}
        data-market-chart-mode={showIndicatorPanels ? "stacked-indicators" : "compact"}
        data-chart-left={left}
        data-chart-right={right}
        data-chart-candle-count={model.baseCandleCount}
        data-chart-visible-start-index={model.visibleStartIndex}
        data-chart-visible-end-index={model.visibleEndIndex}
        className="h-auto w-full"
      >
        <style>
          {`
            .market-candle-zone {
              fill: rgba(37, 99, 235, 0.001);
              outline: none;
            }
            .market-candle-tooltip {
              opacity: 0;
              transition: opacity 120ms ease;
            }
            .market-candle-zone:hover,
            .market-candle-zone:focus {
              fill: rgba(37, 99, 235, 0.08);
            }
            .market-candle-zone:focus-visible {
              stroke: rgba(37, 99, 235, 0.5);
              stroke-width: 1.2;
            }
            .market-candle-zone:hover ~ .market-candle-tooltip,
            .market-candle-zone:focus ~ .market-candle-tooltip,
            .market-candle-hover:hover .market-candle-tooltip,
            .market-candle-hover:focus-within .market-candle-tooltip {
              opacity: 1;
            }
          `}
        </style>
        <rect width={width} height={height} fill="#ffffff" />
        <rect x={left} y={priceTop} width={plotWidth} height={priceHeight} fill="#f8fafc" opacity="0.7" />
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const y = priceTop + ratio * priceHeight;
          return <line key={ratio} x1={left} x2={width - right} y1={y} y2={y} stroke="#e2e8f0" strokeWidth="1" />;
        })}
        {volumeProfile.length > 0 ? (
          <g data-market-volume-profile="true" pointerEvents="none">
            {volumeProfile.map((bin, index) => {
              if (bin.volume <= 0) {
                return null;
              }
              const yHigh = yForPrice(bin.high);
              const yLow = yForPrice(bin.low);
              const barWidth = Math.max(2, 74 * bin.strength);
              return (
                <rect
                  key={`profile-${index}-${bin.low.toFixed(4)}`}
                  x={width - right - barWidth - 4}
                  y={Math.min(yHigh, yLow)}
                  width={barWidth}
                  height={Math.max(1, Math.abs(yLow - yHigh))}
                  rx="2"
                  fill="#94a3b8"
                  opacity="0.2"
                />
              );
            })}
            <text x={width - 78} y={priceTop + 16} className="fill-slate-500 text-[10px] font-semibold">
              매물대
            </text>
          </g>
        ) : null}
        {backendPocPrice !== null ? (
          <g data-market-ai-volume-profile="true" pointerEvents="none">
            <line
              x1={left}
              x2={width - right}
              y1={yForPrice(backendPocPrice)}
              y2={yForPrice(backendPocPrice)}
              stroke="#0f172a"
              strokeDasharray="6 4"
              strokeWidth="1.2"
              opacity="0.7"
            />
            <text x={left + 8} y={yForPrice(backendPocPrice) - 5} className="fill-slate-700 text-[10px] font-semibold">
              AI 최대 매물대 {formatNumber(backendPocPrice, priceDigits)}
            </text>
          </g>
        ) : null}
        {backendValueAreaLow !== null && backendValueAreaHigh !== null ? (
          <g pointerEvents="none">
            <line x1={left} x2={width - right} y1={yForPrice(backendValueAreaLow)} y2={yForPrice(backendValueAreaLow)} stroke="#64748b" strokeDasharray="2 6" strokeWidth="1" opacity="0.55" />
            <line x1={left} x2={width - right} y1={yForPrice(backendValueAreaHigh)} y2={yForPrice(backendValueAreaHigh)} stroke="#64748b" strokeDasharray="2 6" strokeWidth="1" opacity="0.55" />
          </g>
        ) : null}
        {showLevels ? [stats.high, stats.low].map((value, index) => {
          if (value === null) {
            return null;
          }
          const y = yForPrice(value);
          const label = index === 0 ? "고점" : "저점";
          return (
            <g key={`${label}-${value}`}>
              <line x1={left} x2={width - right} y1={y} y2={y} stroke="#cbd5e1" strokeDasharray="4 4" strokeWidth="1" />
              <text x={width - 86} y={y - 4} className="fill-slate-500 text-[10px]">
                {label} {formatNumber(value, priceDigits)}
              </text>
            </g>
          );
        }) : null}
        {showVolume ? <line x1={left} x2={width - right} y1={volumeTop} y2={volumeTop} stroke="#cbd5e1" strokeWidth="1" /> : null}
        {showVolume && upVolumePath.length > 0 ? <path d={upVolumePath.join(" ")} fill="#d1fae5" /> : null}
        {showVolume && downVolumePath.length > 0 ? <path d={downVolumePath.join(" ")} fill="#ffe4e6" /> : null}
        {showAverageVolume && stats.averageVolume !== null ? (
          <g>
            <line
              x1={left}
              x2={width - right}
              y1={yForVolume(stats.averageVolume)}
              y2={yForVolume(stats.averageVolume)}
              stroke="#64748b"
              strokeDasharray="3 4"
              strokeWidth="1"
            />
            {!compact ? (
              <text x={width - 122} y={yForVolume(stats.averageVolume) - 4} className="fill-slate-500 text-[10px]">
                평균 거래량
              </text>
            ) : null}
          </g>
        ) : null}
        {bollingerBandArea ? (
          <polygon data-market-bollinger="true" points={bollingerBandArea} fill="#dbeafe" opacity="0.2" pointerEvents="none" />
        ) : null}
        {bollingerUpperLine ? <polyline points={bollingerUpperLine} fill="none" stroke="#0284c7" strokeWidth="1.2" strokeLinejoin="round" pointerEvents="none" /> : null}
        {bollingerMiddleLine ? <polyline points={bollingerMiddleLine} fill="none" stroke="#64748b" strokeWidth="1" strokeDasharray="4 4" strokeLinejoin="round" pointerEvents="none" /> : null}
        {bollingerLowerLine ? <polyline points={bollingerLowerLine} fill="none" stroke="#0284c7" strokeWidth="1.2" strokeLinejoin="round" pointerEvents="none" /> : null}
        {upWickPath.length > 0 ? <path d={upWickPath.join(" ")} fill="none" stroke="#10b981" strokeWidth="1.4" /> : null}
        {downWickPath.length > 0 ? <path d={downWickPath.join(" ")} fill="none" stroke="#f43f5e" strokeWidth="1.4" /> : null}
        {upBodyPath.length > 0 ? <path d={upBodyPath.join(" ")} fill="#10b981" /> : null}
        {downBodyPath.length > 0 ? <path d={downBodyPath.join(" ")} fill="#f43f5e" /> : null}
        {showCloseLine ? <polyline points={closeLine} fill="none" stroke="#2563eb" strokeWidth="1.8" strokeLinejoin="round" /> : null}
        {visibleMarkers.map((marker) => {
          const colors = markerColors[marker.kind];
          const markerPriceY =
            marker.price !== null
              ? clampSvgY(yForPrice(marker.price), priceTop + 8, priceTop + priceHeight - 8)
              : null;
          const y =
            markerPriceY === null
              ? priceTop + 10 + marker.slot * 16
              : clampSvgY(markerPriceY - 12 - marker.slot * 16, priceTop + 8, priceTop + priceHeight - 8);
          const markerWidth = marker.label.length > 2 ? 34 : 24;
          return (
            <g
              key={`${marker.kind}-${marker.timestamp}-${marker.slot}`}
              data-market-event-marker="true"
              data-market-event-kind={marker.kind}
              data-market-event-status={marker.statusLabel}
            >
              <title>{marketEventMarkerTooltipRows(marker, priceDigits).join("\n")}</title>
              <line x1={marker.x} x2={marker.x} y1={priceTop} y2={priceTop + priceHeight} stroke={colors.fill} strokeDasharray="2 4" strokeWidth="1" opacity="0.55" />
              {markerPriceY !== null ? (
                <circle
                  cx={marker.x}
                  cy={markerPriceY}
                  r="4"
                  fill={colors.fill}
                  stroke="#ffffff"
                  strokeWidth="1.5"
                  data-market-event-price={marker.price}
                />
              ) : null}
              <rect x={marker.x - markerWidth / 2} y={y - 8} width={markerWidth} height="14" rx="5" fill={colors.fill} stroke={colors.stroke} />
              {!compact ? (
                <text x={marker.x} y={y + 2} textAnchor="middle" className="fill-white text-[8px] font-semibold">
                  {marker.label}
                </text>
              ) : null}
            </g>
          );
        })}
        {showIndicatorPanels ? (
          <g data-market-indicator-panels="true">
            <rect x={left} y={rsiTop} width={plotWidth} height={indicatorHeight} fill="#f8fafc" opacity="0.78" />
            <rect x={left} y={atrTop} width={plotWidth} height={indicatorHeight} fill="#f8fafc" opacity="0.78" />
            <rect x={left} y={volatilityTop} width={plotWidth} height={indicatorHeight} fill="#f8fafc" opacity="0.78" />
            {[rsiTop, atrTop, volatilityTop].map((top) => (
              <g key={top}>
                <line x1={left} x2={width - right} y1={top} y2={top} stroke="#cbd5e1" strokeWidth="1" />
                <line x1={left} x2={width - right} y1={top + indicatorHeight} y2={top + indicatorHeight} stroke="#e2e8f0" strokeWidth="1" />
              </g>
            ))}
            <line
              x1={left}
              x2={width - right}
              y1={rsiTop + indicatorHeight * 0.3}
              y2={rsiTop + indicatorHeight * 0.3}
              stroke="#cbd5e1"
              strokeDasharray="3 5"
              strokeWidth="1"
            />
            <line
              x1={left}
              x2={width - right}
              y1={rsiTop + indicatorHeight * 0.7}
              y2={rsiTop + indicatorHeight * 0.7}
              stroke="#cbd5e1"
              strokeDasharray="3 5"
              strokeWidth="1"
            />
            {rsiLine ? <polyline points={rsiLine} fill="none" stroke="#7c3aed" strokeWidth="1.7" strokeLinejoin="round" /> : null}
            {atrLine ? <polyline points={atrLine} fill="none" stroke="#f59e0b" strokeWidth="1.7" strokeLinejoin="round" /> : null}
            {volatilityValues.map((value, index) => {
              const barY = yForVolatility(value);
              return (
                <rect
                  key={`volatility-${candles[index]?.timestamp ?? index}-${index}`}
                  x={xFor(index) - volatilityBarWidth / 2}
                  y={barY}
                  width={volatilityBarWidth}
                  height={Math.max(1, volatilityTop + indicatorHeight - barY)}
                  rx="1.6"
                  fill={index === volatilityValues.length - 1 ? "#2563eb" : "#cbd5e1"}
                />
              );
            })}
            <text x="8" y={rsiTop + 13} className="fill-slate-500 text-[10px] font-semibold">
              RSI
            </text>
            <text x="8" y={atrTop + 13} className="fill-slate-500 text-[10px] font-semibold">
              ATR 비율
            </text>
            <text x="8" y={volatilityTop + 13} className="fill-slate-500 text-[10px] font-semibold">
              변동폭
            </text>
            <text x={width - 138} y={rsiTop + 13} className="fill-slate-600 text-[10px] font-semibold">
              RSI {formatNumber(latestIndicator?.rsi, 1)}
            </text>
            <text x={width - 138} y={atrTop + 13} className="fill-slate-600 text-[10px] font-semibold">
              ATR {formatUnsignedPercent(latestIndicator?.atrPct, 2)}
            </text>
            <text x={width - 138} y={volatilityTop + 13} className="fill-slate-600 text-[10px] font-semibold">
              변동폭 {formatUnsignedPercent(latestIndicator?.volatilityPct, 2)}
            </text>
          </g>
        ) : null}
        {hoverZones.map((zone) => {
          return (
            <g key={`tooltip-${zone.item.timestamp}-${zone.index}`} data-candle-tooltip="true" className="market-candle-hover">
              <rect
                data-candle-index={zone.index}
                data-candle-timestamp={zone.item.timestamp}
                x={zone.x}
                y={priceTop}
                width={zone.width}
                height={hoverHeight}
                pointerEvents="all"
                tabIndex={0}
                aria-label={zone.tooltip}
                className="market-candle-zone cursor-crosshair"
              />
              <g className="market-candle-tooltip pointer-events-none" pointerEvents="none">
                <line
                  x1={zone.center}
                  x2={zone.center}
                  y1={priceTop}
                  y2={indicatorBottom}
                  stroke="#2563eb"
                  strokeDasharray="3 3"
                  strokeWidth="1.2"
                />
                <circle cx={zone.center} cy={yForPrice(zone.item.close)} r="3.2" fill="#2563eb" stroke="#ffffff" strokeWidth="1.4" />
                <rect
                  x={zone.tooltipX}
                  y={zone.tooltipY}
                  width={tooltipWidth}
                  height={zone.tooltipHeight}
                  rx="6"
                  fill="#0f172a"
                  opacity="0.94"
                />
                <text x={zone.tooltipX + 10} y={zone.tooltipY + 18} className="fill-white text-[10px] font-semibold">
                  {zone.tooltipRows.map((row, rowIndex) => (
                    <tspan key={`${zone.index}-${row}`} x={zone.tooltipX + 10} dy={rowIndex === 0 ? 0 : 14}>
                      {row}
                    </tspan>
                  ))}
                </text>
              </g>
            </g>
          );
        })}
        {showLevels && latestY !== null ? (
          <g pointerEvents="none">
            <line x1={left} x2={width - right} y1={latestY} y2={latestY} stroke="#2563eb" strokeDasharray="2 4" strokeWidth="1" />
            <rect
              x={width - 115}
              y={Math.max(priceTop + 4, latestY - 12)}
              width="88"
              height="20"
              rx="6"
              fill={latestToneColors.fill}
              stroke={latestToneColors.stroke}
            />
            <text x={width - 108} y={Math.max(priceTop + 18, latestY + 3)} className="fill-slate-900 text-[10px] font-semibold">
              {formatNumber(latestClose, priceDigits)}
            </text>
          </g>
        ) : null}
        <text x="8" y={priceTop + 5} className="fill-slate-500 text-[11px]" pointerEvents="none">
          {formatNumber(maxPrice, priceDigits)}
        </text>
        <text x="8" y={priceTop + priceHeight + 4} className="fill-slate-500 text-[11px]" pointerEvents="none">
          {formatNumber(minPrice, priceDigits)}
        </text>
        <text x={left} y={height - 12} className="fill-slate-500 text-[11px]" pointerEvents="none">
          {formatDateTime(firstCandle?.timestamp)}
        </text>
        <text x={width - 126} y={height - 25} className="fill-slate-500 text-[10px] font-semibold" pointerEvents="none">
          현재 봉
        </text>
        <text x={width - 126} y={height - 10} className="fill-slate-500 text-[11px]" pointerEvents="none">
          {formatDateTime(lastCandle?.timestamp)}
        </text>
        {showCloseLine ? <text x={width - 96} y={priceTop + 5} className="fill-slate-500 text-[11px]" pointerEvents="none">
          종가선
        </text> : null}
        {showVolume ? <text x={width - 96} y={volumeTop - 8} className="fill-slate-500 text-[11px]" pointerEvents="none">
          거래량
        </text> : null}
      </svg>
    </div>
  );
  return compact ? chart : <MarketChartZoomShell>{chart}</MarketChartZoomShell>;
}
