import type { ReactNode } from "react";

import type {
  MarketBlockedReasonDistributionItem,
  MarketPeriodPricePoint,
} from "../lib/market-period-detail-graphs";
import { marketPriceTrendStats } from "../lib/market-period-detail-graphs";

type MarketPeriodDetailGraphsProps = {
  symbol: string;
  timeframeLabel: string;
  candleWindowLabel: string;
  candles: readonly MarketPeriodPricePoint[];
  priceDigits: number;
  blockedReasonDistribution: readonly MarketBlockedReasonDistributionItem[];
};

const priceGraphWidth = 640;
const priceGraphHeight = 220;
const priceGraphPadding = {
  top: 18,
  right: 58,
  bottom: 34,
  left: 48,
};

function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value.endsWith("Z") ? value : `${value}Z`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
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

function GraphEmptyState({ message }: { message: string }) {
  return (
    <div className="flex min-h-[220px] items-center justify-center rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
      {message}
    </div>
  );
}

function PriceTrendGraph({
  candles,
  priceDigits,
}: {
  candles: readonly MarketPeriodPricePoint[];
  priceDigits: number;
}) {
  const points = candles.filter(
    (candle): candle is { timestamp: string; close: number } =>
      typeof candle.close === "number" && Number.isFinite(candle.close),
  );
  const stats = marketPriceTrendStats(points);
  if (!stats || points.length < 2) {
    return <GraphEmptyState message="가격 흐름을 표시할 캔들 데이터가 부족합니다." />;
  }

  const plotWidth = priceGraphWidth - priceGraphPadding.left - priceGraphPadding.right;
  const plotHeight = priceGraphHeight - priceGraphPadding.top - priceGraphPadding.bottom;
  const rawRange = stats.maxPrice - stats.minPrice;
  const priceRange = rawRange > 0 ? rawRange : Math.max(stats.maxPrice * 0.001, 1);
  const minPrice = stats.minPrice - priceRange * 0.08;
  const maxPrice = stats.maxPrice + priceRange * 0.08;
  const effectiveRange = maxPrice - minPrice || 1;
  const xFor = (index: number) =>
    priceGraphPadding.left + (index / Math.max(points.length - 1, 1)) * plotWidth;
  const yFor = (price: number) =>
    priceGraphPadding.top + ((maxPrice - price) / effectiveRange) * plotHeight;
  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${xFor(index).toFixed(2)} ${yFor(point.close).toFixed(2)}`)
    .join(" ");
  const areaPath = `${path} L ${xFor(points.length - 1).toFixed(2)} ${(priceGraphPadding.top + plotHeight).toFixed(
    2,
  )} L ${xFor(0).toFixed(2)} ${(priceGraphPadding.top + plotHeight).toFixed(2)} Z`;
  const latestPoint = points[points.length - 1];

  return (
    <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
      <svg
        viewBox={`0 0 ${priceGraphWidth} ${priceGraphHeight}`}
        role="img"
        aria-label="선택 기간 가격 흐름 그래프"
        className="h-[220px] w-full"
        preserveAspectRatio="none"
      >
        <rect width={priceGraphWidth} height={priceGraphHeight} fill="#ffffff" />
        <line
          x1={priceGraphPadding.left}
          x2={priceGraphWidth - priceGraphPadding.right}
          y1={priceGraphPadding.top + plotHeight}
          y2={priceGraphPadding.top + plotHeight}
          stroke="#cbd5e1"
          strokeWidth="1"
        />
        <line
          x1={priceGraphPadding.left}
          x2={priceGraphPadding.left}
          y1={priceGraphPadding.top}
          y2={priceGraphPadding.top + plotHeight}
          stroke="#cbd5e1"
          strokeWidth="1"
        />
        <path d={areaPath} fill="#dbeafe" opacity="0.55" />
        <path d={path} fill="none" stroke="#2563eb" strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
        {latestPoint ? (
          <circle cx={xFor(points.length - 1)} cy={yFor(latestPoint.close)} r="4" fill="#2563eb" stroke="#ffffff" strokeWidth="2" />
        ) : null}
        <text x={priceGraphPadding.left} y={priceGraphHeight - 10} className="fill-slate-500 text-[10px]">
          {formatDateTime(stats.firstTimestamp)}
        </text>
        <text x={priceGraphWidth - priceGraphPadding.right} y={priceGraphHeight - 10} textAnchor="end" className="fill-slate-500 text-[10px]">
          {formatDateTime(stats.lastTimestamp)}
        </text>
        <text x={priceGraphWidth - 10} y={priceGraphPadding.top + 4} textAnchor="end" className="fill-slate-500 text-[10px]">
          {formatNumber(stats.maxPrice, priceDigits)}
        </text>
        <text x={priceGraphWidth - 10} y={priceGraphPadding.top + plotHeight} textAnchor="end" className="fill-slate-500 text-[10px]">
          {formatNumber(stats.minPrice, priceDigits)}
        </text>
      </svg>
    </div>
  );
}

function BlockedReasonDistributionGraph({
  items,
}: {
  items: readonly MarketBlockedReasonDistributionItem[];
}) {
  if (items.length === 0) {
    return <GraphEmptyState message="표시할 신규진입 차단 사유가 없습니다." />;
  }

  const visibleItems = items.slice(0, 6);
  const hiddenCount = Math.max(0, items.length - visibleItems.length);
  const maxCount = Math.max(...visibleItems.map((item) => item.count), 1);

  return (
    <div className="min-h-[220px] rounded-md border border-slate-200 bg-white px-4 py-4">
      <div className="space-y-3">
        {visibleItems.map((item) => {
          const percent = Math.max(8, (item.count / maxCount) * 100);
          return (
            <div key={item.key} title={`원본 코드: ${item.codes.join(", ")}`}>
              <div className="flex items-start justify-between gap-3 text-xs">
                <span className="min-w-0 flex-1 break-words font-medium text-slate-700">{item.label}</span>
                <span className="shrink-0 font-semibold text-slate-950">{item.count}회</span>
              </div>
              <div className="mt-1 h-2.5 overflow-hidden rounded-full bg-slate-100">
                <div className="h-full rounded-full bg-rose-500" style={{ width: `${percent}%` }} />
              </div>
              <p className="mt-1 break-words text-[11px] leading-4 text-slate-400">원본 코드: {item.codes.join(", ")}</p>
            </div>
          );
        })}
      </div>
      {hiddenCount > 0 ? (
        <p className="mt-3 text-xs text-slate-500">외 {hiddenCount}개 사유는 원본 데이터에 포함되어 있습니다.</p>
      ) : null}
    </div>
  );
}

function GraphCard({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
      <div>
        <h4 className="text-sm font-semibold text-slate-950">{title}</h4>
        <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
      </div>
      <div className="mt-3">{children}</div>
    </div>
  );
}

export function MarketPeriodDetailGraphs({
  symbol,
  timeframeLabel,
  candleWindowLabel,
  candles,
  priceDigits,
  blockedReasonDistribution,
}: MarketPeriodDetailGraphsProps) {
  return (
    <section className="mt-4 rounded-md border border-slate-200 bg-white p-4" aria-label="기간별 상세">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">기간별 상세</p>
          <h3 className="mt-1 text-base font-semibold text-slate-950">선택 기간 추세 요약</h3>
        </div>
        <span className="w-fit rounded-md bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-600">
          {symbol} / {timeframeLabel} / {candleWindowLabel}
        </span>
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <GraphCard
          title="기간별 가격 흐름"
          description="선택된 심볼, 봉 기준, 조회 캔들 수에 맞춘 종가 흐름입니다."
        >
          <PriceTrendGraph candles={candles} priceDigits={priceDigits} />
        </GraphCard>
        <GraphCard
          title="신규진입 차단 사유 분포"
          description="선택 기간 안에서 리스크 차단으로 기록된 사유를 한글 라벨별로 집계합니다."
        >
          <BlockedReasonDistributionGraph items={blockedReasonDistribution} />
        </GraphCard>
      </div>
    </section>
  );
}
