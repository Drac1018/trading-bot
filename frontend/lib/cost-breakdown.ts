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

const costMetricLabels: Record<string, string> = {
  fee: "수수료",
  fee_usdt: "수수료",
  maker_fee: "Maker 수수료",
  maker_fee_usdt: "Maker 수수료",
  taker_fee: "Taker 수수료",
  taker_fee_usdt: "Taker 수수료",
  funding_fee: "펀딩비",
  funding: "펀딩비",
  funding_usdt: "펀딩비",
  slippage: "슬리피지",
  signed_slippage: "평균 슬리피지",
  signed_slippage_bps: "평균 슬리피지",
  adverse_slippage: "불리한 슬리피지",
  adverse_slippage_bps: "불리한 슬리피지",
  realized_pnl: "실현 손익",
  realized_pnl_usdt: "실현 손익",
  unrealized_pnl: "미실현 손익",
  unrealized_pnl_usdt: "미실현 손익",
  net_pnl: "순손익",
  net_pnl_usdt: "순손익",
  gross_pnl: "총손익",
  gross_pnl_usdt: "총손익",
  total_cost: "총 비용",
  total_cost_usdt: "총 비용",
  estimated_cost: "예상 비용",
  estimated_cost_usdt: "예상 비용",
  fee_ratio_pct: "수수료 / 총손익",
  total_cost_ratio_pct: "총 비용 / 총손익",
};

const costMetricDescriptions: Record<string, string> = {
  fee_usdt: "체결 수수료를 USDT 기준으로 합산한 값입니다.",
  maker_fee: "Maker 주문에서 발생한 수수료입니다.",
  taker_fee: "Taker 주문에서 발생한 수수료입니다.",
  funding_usdt: "펀딩 정산 금액입니다. 양수는 수취, 음수는 비용입니다.",
  signed_slippage_bps: "체결 방향을 반영한 평균 슬리피지입니다. 양수는 불리한 체결입니다.",
  adverse_slippage_bps: "불리한 방향의 체결 차이만 모은 평균 슬리피지입니다.",
  realized_pnl: "청산 체결까지 반영된 확정 손익입니다.",
  unrealized_pnl: "아직 청산되지 않은 포지션의 평가 손익입니다.",
  net_pnl_usdt: "총손익에서 수수료와 펀딩비 등 비용을 반영한 값입니다.",
  gross_pnl_usdt: "비용 차감 전 실현 손익입니다.",
  total_cost_usdt: "수수료, 펀딩비, 불리한 체결 비용을 합산한 비용입니다.",
  fee_ratio_pct: "총손익 대비 수수료 비율입니다.",
  total_cost_ratio_pct: "총손익 대비 총 비용 비율입니다.",
};

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

function signedPrefix(value: number) {
  return value > 0 ? "+" : "";
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
    section: "cost",
    period: selection.period,
    year: String(selection.year),
  });
  if (selection.period === "month") {
    params.set("month", String(selection.month));
  }
  return `/dashboard/analytics?${params.toString()}`;
}

export function costMetricLabel(key: string | null | undefined) {
  if (!key) {
    return "알 수 없는 비용 항목";
  }
  return costMetricLabels[key] ?? "알 수 없는 비용 항목";
}

export function costMetricDescription(key: string | null | undefined) {
  if (!key) {
    return "항목 설명을 확인할 수 없습니다.";
  }
  return costMetricDescriptions[key] ?? "아직 표시 라벨에 등록되지 않은 비용 항목입니다.";
}

export function formatCostBreakdownUsdt(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${signedPrefix(value)}${value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })} USDT`;
}

export function formatCostBreakdownPercent(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "N/A";
  }
  return `${signedPrefix(value)}${value.toLocaleString("ko-KR", {
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
  return `${signedPrefix(value)}${value.toLocaleString("ko-KR", {
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
    NO_SAMPLE: "표본 없음",
    STALE: "오래됨",
    UNKNOWN: "확인 필요",
  };
  return value ? labels[value] ?? "확인 필요" : "확인 필요";
}

export function slippageWeightingLabel(value: string | null | undefined) {
  const labels: Record<string, string> = {
    quantity: "수량 가중",
    notional: "명목금액 가중",
    equal: "동일 가중",
  };
  return value ? labels[value] ?? "알 수 없는 가중 방식" : "N/A";
}

export function costBreakdownQualityBadges(dataQuality: AnalyticsCostBreakdownDataQuality) {
  const badges: Array<{ label: string; tone: "good" | "warn" | "danger" | "neutral" }> = [];

  if (dataQuality.realized_pnl_confirmed) {
    badges.push({ label: "실현 손익 확정", tone: "good" });
  } else {
    badges.push({ label: "실현 손익 미확정", tone: "danger" });
  }

  if (dataQuality.missing_close_execution_count > 0) {
    badges.push({ label: `청산 체결 누락 ${dataQuality.missing_close_execution_count}건`, tone: "danger" });
  }

  if (dataQuality.execution_sync_status !== "COMPLETE") {
    badges.push({ label: `체결 동기화 ${statusLabel(dataQuality.execution_sync_status)}`, tone: "warn" });
  }

  if (dataQuality.funding_sync_status !== "COMPLETE") {
    badges.push({ label: `펀딩 동기화 ${statusLabel(dataQuality.funding_sync_status)}`, tone: "warn" });
  }

  if (dataQuality.slippage_data_status !== "COMPLETE") {
    badges.push({
      label: dataQuality.slippage_data_status === "NO_SAMPLE" ? "슬리피지 표본 없음" : "슬리피지 데이터 부족",
      tone: "warn",
    });
  }

  return badges;
}

export function costBreakdownWarningMessages(payload: AnalyticsCostBreakdownResponse) {
  const messages: string[] = payload.warnings.map((warning) => {
    if (warning.startsWith("missing_close_execution_count")) {
      return "청산 체결 누락으로 실현 손익이 확정되지 않았습니다.";
    }
    if (warning.startsWith("funding_sync_status")) {
      return "펀딩비 동기화가 불완전합니다.";
    }
    if (warning.startsWith("slippage_data_status")) {
      if (warning.includes("NO_SAMPLE")) {
        return "해당 기간에 체결 표본이 없어 평균 체결 불리도를 계산할 수 없습니다.";
      }
      return "슬리피지 데이터가 부족해 평균 체결 불리도를 확정할 수 없습니다.";
    }
    if (warning.startsWith("fee_asset_unconverted")) {
      return "USDT로 환산하지 못한 수수료 자산이 있습니다.";
    }
    return "알 수 없는 비용 경고";
  });

  if (payload.summary.fee_ratio_pct !== null && payload.summary.fee_ratio_pct >= 15) {
    messages.unshift(`수수료가 총손익의 ${formatCostBreakdownPercent(payload.summary.fee_ratio_pct)}를 차지합니다.`);
  }

  return [...new Set(messages)];
}

export function costBreakdownBucketStatus(
  bucket: AnalyticsCostBreakdownBucket,
  dataQuality: AnalyticsCostBreakdownDataQuality,
) {
  const statuses: string[] = [];
  if (!dataQuality.realized_pnl_confirmed) {
    statuses.push("실현 손익 미확정");
  }
  if (dataQuality.missing_close_execution_count > 0) {
    statuses.push("청산 체결 누락");
  }
  if (dataQuality.funding_sync_status !== "COMPLETE") {
    statuses.push("펀딩비 미확정");
  }
  if (dataQuality.slippage_data_status !== "COMPLETE") {
    statuses.push(dataQuality.slippage_data_status === "NO_SAMPLE" ? "슬리피지 표본 없음" : "슬리피지 데이터 부족");
  }
  if (bucket.gross_pnl_usdt <= 0 && (bucket.fee_usdt > 0 || bucket.total_cost_usdt > 0)) {
    statuses.push("비율 N/A");
  }
  return statuses.length > 0 ? statuses.join(" · ") : "정상";
}
