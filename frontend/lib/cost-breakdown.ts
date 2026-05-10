export type CostBreakdownPeriod = "today" | "month" | "year";

export type AnalyticsCostBreakdownSummary = {
  net_pnl_usdt: number;
  gross_pnl_usdt: number;
  fee_usdt: number;
  funding_usdt: number;
  total_cost_usdt: number;
  fee_ratio_pct: number | null;
  total_cost_ratio_pct: number | null;
  signed_slippage_bps: number | null;
  adverse_slippage_bps: number | null;
};

export type AnalyticsCostBreakdownBucket = AnalyticsCostBreakdownSummary & {
  label: string;
  start_at: string;
  end_at: string;
};

export type AnalyticsCostBreakdownDataQuality = {
  realized_pnl_confirmed: boolean;
  execution_sync_status: string;
  funding_sync_status: string;
  slippage_data_status: string;
  missing_close_execution_count: number;
  slippage_weighting?: string;
};

export type AnalyticsCostBreakdownResponse = {
  period: CostBreakdownPeriod;
  timezone: string;
  start_at: string;
  end_at: string;
  summary: AnalyticsCostBreakdownSummary;
  buckets: AnalyticsCostBreakdownBucket[];
  data_quality: AnalyticsCostBreakdownDataQuality;
  warnings: string[];
};

export type CostBreakdownSelection = {
  period: CostBreakdownPeriod;
  year: number;
  month: number;
};

const supportedPeriods = new Set<CostBreakdownPeriod>(["today", "month", "year"]);

function firstQueryValue(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

function parseBoundedInt(value: string | string[] | undefined, fallback: number, min: number, max: number) {
  const parsed = Number(firstQueryValue(value));
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    return fallback;
  }
  return parsed;
}

function seoulTodayParts(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "numeric",
  }).formatToParts(now);
  const year = Number(parts.find((part) => part.type === "year")?.value);
  const month = Number(parts.find((part) => part.type === "month")?.value);
  return {
    year: Number.isInteger(year) ? year : now.getUTCFullYear(),
    month: Number.isInteger(month) ? month : now.getUTCMonth() + 1,
  };
}

export function resolveCostBreakdownSelection(
  query: Record<string, string | string[] | undefined>,
  now = new Date(),
): CostBreakdownSelection {
  const current = seoulTodayParts(now);
  const requestedPeriod = firstQueryValue(query.period);
  const period = supportedPeriods.has(requestedPeriod as CostBreakdownPeriod)
    ? (requestedPeriod as CostBreakdownPeriod)
    : "month";

  return {
    period,
    year: parseBoundedInt(query.year, current.year, 2020, 2100),
    month: parseBoundedInt(query.month, current.month, 1, 12),
  };
}

export function buildCostBreakdownApiPath(selection: CostBreakdownSelection) {
  const params = new URLSearchParams({
    period: selection.period,
    year: String(selection.year),
  });
  if (selection.period === "month") {
    params.set("month", String(selection.month));
  }
  return `/api/analytics/cost-breakdown?${params.toString()}`;
}

export function buildCostBreakdownPageHref(selection: CostBreakdownSelection) {
  const params = new URLSearchParams({
    period: selection.period,
    year: String(selection.year),
  });
  if (selection.period === "month") {
    params.set("month", String(selection.month));
  }
  return `/dashboard/cost-breakdown?${params.toString()}`;
}

export function formatCostBreakdownUsdt(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })} USDT`;
}

export function formatCostBreakdownPercent(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "N/A";
  }
  return `${value.toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 1,
  })}%`;
}

export function formatCostBreakdownBps(
  value: number | null | undefined,
  slippageStatus: string | null | undefined = "COMPLETE",
) {
  if (slippageStatus !== "COMPLETE" || value === null || value === undefined || Number.isNaN(value)) {
    return "N/A";
  }
  return `${value.toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })} bps`;
}

export function formatCostBreakdownDateTime(value: string | null | undefined, timezone = "Asia/Seoul") {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

export function statusLabel(value: string | null | undefined) {
  const labels: Record<string, string> = {
    COMPLETE: "완료",
    INCOMPLETE: "불완전",
    STALE: "오래됨",
    UNKNOWN: "확인 필요",
  };
  return value ? labels[value] ?? value : "확인 필요";
}

export function costBreakdownQualityBadges(dataQuality: AnalyticsCostBreakdownDataQuality) {
  const badges: Array<{ label: string; tone: "good" | "warn" | "danger" | "neutral" }> = [];

  if (dataQuality.realized_pnl_confirmed) {
    badges.push({ label: "Realized PnL 확인됨", tone: "good" });
  } else {
    badges.push({ label: "PnL 미확정", tone: "danger" });
  }

  if (dataQuality.missing_close_execution_count > 0) {
    badges.push({ label: `청산 체결 누락 ${dataQuality.missing_close_execution_count}건`, tone: "danger" });
  }

  if (dataQuality.execution_sync_status !== "COMPLETE") {
    badges.push({ label: `체결 동기화 ${statusLabel(dataQuality.execution_sync_status)}`, tone: "warn" });
  }

  if (dataQuality.funding_sync_status !== "COMPLETE") {
    badges.push({ label: `Funding ${statusLabel(dataQuality.funding_sync_status)}`, tone: "warn" });
  }

  if (dataQuality.slippage_data_status !== "COMPLETE") {
    badges.push({ label: "Slippage 데이터 부족", tone: "warn" });
  }

  return badges;
}

export function costBreakdownWarningMessages(payload: AnalyticsCostBreakdownResponse) {
  const messages = payload.warnings.map((warning) => {
    if (warning.startsWith("missing_close_execution_count")) {
      return "청산 체결 누락으로 realized PnL이 확정되지 않았습니다.";
    }
    if (warning.startsWith("funding_sync_status")) {
      return "Funding 동기화가 불완전합니다.";
    }
    if (warning.startsWith("slippage_data_status")) {
      return "Slippage 데이터가 부족하여 평균 체결 불리도를 확정할 수 없습니다.";
    }
    if (warning.startsWith("fee_asset_unconverted")) {
      return "USDT로 환산하지 못한 수수료 asset이 있습니다.";
    }
    return warning;
  });

  if (payload.summary.fee_ratio_pct !== null && payload.summary.fee_ratio_pct >= 15) {
    messages.unshift(`수수료가 gross PnL의 ${formatCostBreakdownPercent(payload.summary.fee_ratio_pct)}를 차지합니다.`);
  }

  return [...new Set(messages)];
}

export function costBreakdownBucketStatus(
  bucket: AnalyticsCostBreakdownBucket,
  dataQuality: AnalyticsCostBreakdownDataQuality,
) {
  const statuses: string[] = [];
  if (!dataQuality.realized_pnl_confirmed) {
    statuses.push("PnL 미확정");
  }
  if (dataQuality.missing_close_execution_count > 0) {
    statuses.push("청산 체결 누락");
  }
  if (dataQuality.funding_sync_status !== "COMPLETE") {
    statuses.push("Funding 미확정");
  }
  if (dataQuality.slippage_data_status !== "COMPLETE") {
    statuses.push("Slippage 데이터 부족");
  }
  if (bucket.gross_pnl_usdt <= 0 && (bucket.fee_usdt > 0 || bucket.total_cost_usdt > 0)) {
    statuses.push("비율 N/A");
  }
  return statuses.length > 0 ? statuses.join(" · ") : "정상";
}
