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
  slippage_data_status?: string | null;
  slippage_data_reason?: string | null;
  funding_sync_status?: string | null;
  funding_sync_reason?: string | null;
  slippage_sample_count?: number | null;
  missing_slippage_sample_count?: number | null;
  warning_codes?: string[] | null;
};

export type AnalyticsCostBreakdownDataQuality = {
  realized_pnl_confirmed: boolean;
  execution_sync_status: string;
  funding_sync_status: string;
  funding_sync_reason?: string | null;
  slippage_data_status: string;
  slippage_data_reason?: string | null;
  slippage_sample_count?: number | null;
  missing_slippage_sample_count?: number | null;
  missing_close_execution_count: number;
  slippage_weighting?: string;
  warning_codes?: string[] | null;
};

export type SlippageDataQualityLike = {
  slippage_data_status?: string | null;
  slippage_data_reason?: string | null;
  slippage_sample_count?: number | null;
  missing_slippage_sample_count?: number | null;
};

export type ProfitabilityCostStatusLike = SlippageDataQualityLike & {
  status?: string | null;
  net_pnl?: number | null;
  execution_sync_status?: string | null;
  funding_sync_status?: string | null;
  warning_codes?: string[] | null;
};

export type ProfitabilityCostTone = "safe" | "warn" | "danger" | "neutral";
export type CostBreakdownTone = "good" | "warn" | "danger" | "neutral";

export type ProfitabilityReadinessLike = {
  status?: string | null;
  reason_codes?: string[] | null;
};

export type AnalyticsCostBreakdownResponse = {
  period: CostBreakdownPeriod;
  timezone: string;
  start_at: string;
  end_at: string;
  summary: AnalyticsCostBreakdownSummary;
  buckets: AnalyticsCostBreakdownBucket[];
  data_quality: AnalyticsCostBreakdownDataQuality;
  warnings?: string[] | null;
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
  if (normalizeSlippageDataStatus(slippageStatus) !== "COMPLETE" || value === null || value === undefined || Number.isNaN(value)) {
    return "N/A";
  }
  return `${signedPrefix(value)}${value.toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })} bps`;
}

export function formatProfitabilityCostSlippageBps(
  value: number | null | undefined,
  cost: ProfitabilityCostStatusLike | null | undefined,
  unavailableLabel = "-",
) {
  const status = cost ? profitabilityCostSlippageStatus(cost) : "UNKNOWN";
  const formatted = formatCostBreakdownBps(value, status);
  return formatted === "N/A" ? unavailableLabel : formatted;
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
    NOT_READY: "준비 안 됨",
    BLOCKED: "차단됨",
    STALE: "오래됨",
    UNKNOWN: "확인 필요",
  };
  return labels[normalizeStatusCode(value)] ?? "확인 필요";
}

function normalizeStatusCode(value: string | null | undefined) {
  return value?.trim().toUpperCase() || "UNKNOWN";
}

export function costBreakdownStatusTone(value: string | null | undefined): CostBreakdownTone {
  const status = normalizeStatusCode(value);
  if (status === "COMPLETE") {
    return "good";
  }
  if (status === "UNKNOWN" || status === "NOT_READY" || status === "BLOCKED") {
    return "danger";
  }
  return "warn";
}

export function normalizeSlippageDataStatus(value: string | null | undefined) {
  return normalizeStatusCode(value);
}

function publishedSlippageDataStatus(value: string | null | undefined) {
  const normalized = value?.trim().toUpperCase();
  return normalized && normalized.length > 0 ? normalized : null;
}

function inferredSlippageDataStatus(value: SlippageDataQualityLike) {
  const reason = value.slippage_data_reason?.trim().toLowerCase();
  if (reason === "no_execution_slippage_sample") {
    return "NO_SAMPLE";
  }
  if (reason === "missing_execution_slippage_sample" || reason === "no_valid_slippage_sample") {
    return "INCOMPLETE";
  }
  if (reason === "slippage_not_ready") {
    return "NOT_READY";
  }
  if (reason === "slippage_blocked") {
    return "BLOCKED";
  }
  if (reason === "slippage_status_unknown") {
    return "UNKNOWN";
  }
  if ((value.missing_slippage_sample_count ?? 0) > 0) {
    return "INCOMPLETE";
  }
  if (value.slippage_sample_count === 0) {
    return "NO_SAMPLE";
  }
  return null;
}

function resolvePublishedOrInferredSlippageStatus(published: string | null, inferred: string | null) {
  if (published === "UNKNOWN" && inferred && inferred !== "UNKNOWN") {
    return inferred;
  }
  return published ?? inferred;
}

function slippageDataStatusFromQuality(value: SlippageDataQualityLike) {
  const published = publishedSlippageDataStatus(value.slippage_data_status);
  const inferred = inferredSlippageDataStatus(value);
  return normalizeSlippageDataStatus(
    resolvePublishedOrInferredSlippageStatus(published, inferred),
  );
}

export function slippageDataQualityStatus(value: SlippageDataQualityLike) {
  return slippageDataStatusFromQuality(value);
}

export function slippageDataReasonLabel(value: string | null | undefined) {
  const labels: Record<string, string> = {
    no_execution_slippage_sample: "체결 표본 없음",
    missing_execution_slippage_sample: "일부 체결 슬리피지 누락",
    no_valid_slippage_sample: "유효 슬리피지 표본 없음",
    slippage_not_ready: "슬리피지 준비 상태 부족",
    slippage_blocked: "슬리피지 게시 차단",
    slippage_status_unknown: "상태 원인 미확정",
  };
  const normalized = value?.trim().toLowerCase();
  return normalized ? labels[normalized] ?? value?.trim() : null;
}

export function slippageDataQualityLabel(dataQuality: SlippageDataQualityLike) {
  const status = slippageDataStatusFromQuality(dataQuality);
  if (status === "COMPLETE") {
    const sampleCount = dataQuality.slippage_sample_count ?? 0;
    return `슬리피지 표본 ${sampleCount.toLocaleString("ko-KR")}건`;
  }
  const reason = slippageDataReasonLabel(dataQuality.slippage_data_reason);
  const missingCount = dataQuality.missing_slippage_sample_count ?? 0;
  const base =
    status === "NO_SAMPLE"
      ? "슬리피지 표본 없음"
      : status === "UNKNOWN"
        ? "슬리피지 상태 확인 필요"
        : status === "NOT_READY"
          ? "슬리피지 준비 부족"
          : status === "BLOCKED"
            ? "슬리피지 게시 차단"
            : "슬리피지 데이터 부족";
  if (reason && missingCount > 0) {
    return `${base}: ${reason} ${missingCount.toLocaleString("ko-KR")}건`;
  }
  return reason ? `${base}: ${reason}` : base;
}

export function slippageDataQualityTone(value: string | null | undefined): "warn" | "danger" {
  const status = normalizeSlippageDataStatus(value);
  return status === "UNKNOWN" || status === "NOT_READY" || status === "BLOCKED" ? "danger" : "warn";
}

export function costBreakdownBucketSlippageStatus(
  bucket: SlippageDataQualityLike,
  dataQuality: SlippageDataQualityLike,
) {
  const bucketStatus = publishedSlippageDataStatus(bucket.slippage_data_status);
  const inferredBucketStatus = hasBucketSlippagePublication(bucket) ? inferredSlippageDataStatus(bucket) : null;
  return normalizeSlippageDataStatus(
    resolvePublishedOrInferredSlippageStatus(bucketStatus, inferredBucketStatus) ??
      slippageDataStatusFromQuality(dataQuality),
  );
}

function hasSlippagePublicationValue(value: string | number | null | undefined) {
  if (typeof value === "string") {
    return value.trim().length > 0;
  }
  return value !== null && value !== undefined;
}

function hasBucketSlippagePublication(bucket: SlippageDataQualityLike) {
  return (
    hasSlippagePublicationValue(bucket.slippage_data_status) ||
    hasSlippagePublicationValue(bucket.slippage_data_reason) ||
    hasSlippagePublicationValue(bucket.slippage_sample_count) ||
    hasSlippagePublicationValue(bucket.missing_slippage_sample_count)
  );
}

function profitabilityCostHasSlippageGap(cost: ProfitabilityCostStatusLike) {
  return profitabilityCostSlippageStatus(cost) !== "COMPLETE";
}

function profitabilityCostHasBlockingSlippageStatus(cost: ProfitabilityCostStatusLike) {
  const status = profitabilityCostSlippageStatus(cost);
  return status === "UNKNOWN" || status === "NOT_READY" || status === "BLOCKED";
}

function normalizeProfitabilityCostStatus(value: string | null | undefined) {
  return value?.trim().toLowerCase() || "";
}

export function profitabilityCostHasNoDataStatus(value: string | null | undefined) {
  return normalizeProfitabilityCostStatus(value) === "no_data";
}

function profitabilityCostSlippageStatus(cost: ProfitabilityCostStatusLike) {
  const published = publishedSlippageDataStatus(cost.slippage_data_status);
  const inferred = inferredSlippageDataStatus(cost);
  return normalizeSlippageDataStatus(
    resolvePublishedOrInferredSlippageStatus(published, inferred),
  );
}

export function profitabilityCostWarningCodes(cost: ProfitabilityCostStatusLike | null | undefined) {
  if (!cost) {
    return [];
  }
  const warningCodes: string[] = [];
  for (const code of cost.warning_codes ?? []) {
    appendNormalizedWarningCode(warningCodes, code);
  }
  const rawExecutionStatus = cost.execution_sync_status?.trim();
  if (rawExecutionStatus) {
    const executionStatus = normalizeStatusCode(rawExecutionStatus);
    if (executionStatus !== "COMPLETE") {
      appendNormalizedWarningCode(warningCodes, `execution_sync_status:${executionStatus}`);
    }
  }
  const rawFundingStatus = cost.funding_sync_status?.trim();
  if (rawFundingStatus) {
    const fundingStatus = normalizeStatusCode(rawFundingStatus);
    if (fundingStatus !== "COMPLETE") {
      const fundingWarningCode = `funding_sync_status:${fundingStatus}`;
      appendNormalizedWarningCode(warningCodes, fundingWarningCode);
    }
  }
  const slippageStatus = profitabilityCostSlippageStatus(cost);
  if (slippageStatus !== "COMPLETE") {
    const slippageWarningCode = `slippage_data_status:${slippageStatus}`;
    appendNormalizedWarningCode(warningCodes, slippageWarningCode);
  }
  return warningCodes;
}

export function profitabilityPublicationWarningCodes(
  cost: ProfitabilityCostStatusLike | null | undefined,
  readiness: ProfitabilityReadinessLike | null | undefined,
) {
  const costWarnings = profitabilityCostWarningCodes(cost);
  const warningCodes = [...costWarnings];
  const costSlippageStatus = cost ? publishedSlippageDataStatus(cost.slippage_data_status) : null;
  const costExecutionStatus = cost?.execution_sync_status?.trim().toUpperCase() || null;
  const costFundingStatus = cost?.funding_sync_status?.trim().toUpperCase() || null;
  const costHasExplicitCompleteSlippage = costSlippageStatus === "COMPLETE";
  const costHasExplicitCompleteExecution = costExecutionStatus === "COMPLETE";
  const costHasExplicitCompleteFunding = costFundingStatus === "COMPLETE";

  for (const reasonCode of readiness?.reason_codes ?? []) {
    const normalizedCode = normalizeProfitabilityWarningCode(reasonCode);
    const [prefix, value] = normalizedCode.split(":", 2);
    if (
      !value ||
      (
        prefix !== "execution_sync_status" &&
        prefix !== "funding_sync_status" &&
        prefix !== "slippage_data_status"
      )
    ) {
      continue;
    }
    if (prefix === "slippage_data_status" && costHasExplicitCompleteSlippage) {
      continue;
    }
    if (prefix === "execution_sync_status" && costHasExplicitCompleteExecution) {
      continue;
    }
    if (prefix === "funding_sync_status" && costHasExplicitCompleteFunding) {
      continue;
    }
    const existingCode = warningCodes.find((warningCode) => warningCode.startsWith(`${prefix}:`));
    if (existingCode && !existingCode.endsWith(":UNKNOWN")) {
      continue;
    }
    appendNormalizedWarningCode(warningCodes, normalizedCode);
  }
  return warningCodes;
}

export function profitabilityCostStatusLabel(cost: ProfitabilityCostStatusLike | null | undefined) {
  if (!cost) {
    return "데이터 없음";
  }
  const hasWarnings = profitabilityCostWarningCodes(cost).length > 0;
  const hasSlippageGap = profitabilityCostHasSlippageGap(cost);
  if (profitabilityCostHasNoDataStatus(cost.status)) {
    return hasWarnings || hasSlippageGap ? "데이터 확인 필요" : "데이터 없음";
  }
  return hasWarnings || hasSlippageGap ? "비용 경고" : "정상";
}

export function profitabilityCostTone(cost: ProfitabilityCostStatusLike | null | undefined): ProfitabilityCostTone {
  if (!cost) {
    return "neutral";
  }
  const warningCodes = profitabilityCostWarningCodes(cost);
  const hasWarnings = warningCodes.length > 0;
  const hasSlippageGap = profitabilityCostHasSlippageGap(cost);
  const hasBlockingSlippageStatus = profitabilityCostHasBlockingSlippageStatus(cost);
  if (profitabilityCostHasNoDataStatus(cost.status)) {
    if (hasBlockingSlippageStatus) {
      return "danger";
    }
    return hasWarnings || hasSlippageGap ? "warn" : "neutral";
  }
  if (hasBlockingSlippageStatus) {
    return "danger";
  }
  if (warningCodes.includes("positive_gross_negative_net") || (cost.net_pnl ?? 0) < 0) {
    return "danger";
  }
  return hasWarnings || hasSlippageGap ? "warn" : "safe";
}

export function profitabilityReadinessTone(readiness: ProfitabilityReadinessLike | null | undefined): ProfitabilityCostTone {
  const status = normalizeProfitabilityReadinessStatus(readiness?.status);
  if (!status) {
    return "neutral";
  }
  if (status === "blocked" || status === "not_ready") {
    return "danger";
  }
  if (status === "limited_live_candidate" || status === "scale_up_candidate") {
    return "safe";
  }
  return "warn";
}

function normalizeProfitabilityReadinessStatus(value: string | null | undefined) {
  return value?.trim().toLowerCase() || "";
}

function profitabilityReadinessHasBlockingStatus(readiness: ProfitabilityReadinessLike | null | undefined) {
  const status = normalizeProfitabilityReadinessStatus(readiness?.status);
  return status === "blocked" || status === "not_ready";
}

function profitabilityReadinessReasonCodeSet(readiness: ProfitabilityReadinessLike | null | undefined) {
  return new Set(
    (readiness?.reason_codes ?? [])
      .map((code) => code?.trim().toLowerCase())
      .filter((code): code is string => Boolean(code)),
  );
}

export function profitabilityReadinessStatusLabel(readiness: ProfitabilityReadinessLike | null | undefined) {
  const rawStatus = readiness?.status?.trim();
  const status = normalizeProfitabilityReadinessStatus(rawStatus);
  if (!status) {
    return "데이터 없음";
  }
  if (
    status === "not_ready" &&
    profitabilityReadinessReasonCodeSet(readiness).has("productization_profitability_unverified")
  ) {
    return "제품화 수익성 검증 부족";
  }
  const labels: Record<string, string> = {
    not_ready: "제품화 준비 미달",
    watch: "제품화 관찰 필요",
    limited_live_candidate: "제한 실주문 후보",
    scale_up_candidate: "확대 후보",
    blocked: "제품화 차단",
  };
  return labels[status] ?? rawStatus ?? status;
}

export function profitabilityPublicationStatusLabel(
  cost: ProfitabilityCostStatusLike | null | undefined,
  readiness: ProfitabilityReadinessLike | null | undefined,
) {
  if (profitabilityReadinessHasBlockingStatus(readiness)) {
    return profitabilityReadinessStatusLabel(readiness);
  }
  if (cost) {
    return profitabilityCostStatusLabel(cost);
  }
  return profitabilityReadinessStatusLabel(readiness);
}

export function profitabilityPublicationTone(
  cost: ProfitabilityCostStatusLike | null | undefined,
  readiness: ProfitabilityReadinessLike | null | undefined,
): ProfitabilityCostTone {
  if (!cost) {
    return profitabilityReadinessTone(readiness);
  }
  if (profitabilityReadinessHasBlockingStatus(readiness)) {
    return "danger";
  }
  const warningCodes = profitabilityPublicationWarningCodes(cost, readiness);
  const hasWarnings = warningCodes.length > 0;
  const hasSlippageGap = profitabilityCostHasSlippageGap(cost);
  const hasBlockingSlippageStatus = warningCodes.some((warningCode) => {
    const normalizedCode = normalizeProfitabilityWarningCode(warningCode);
    return (
      normalizedCode === "slippage_data_status:UNKNOWN" ||
      normalizedCode === "slippage_data_status:NOT_READY" ||
      normalizedCode === "slippage_data_status:BLOCKED"
    );
  });
  const hasBlockingExecutionStatus = warningCodes.some((warningCode) => {
    const normalizedCode = normalizeProfitabilityWarningCode(warningCode);
    if (!normalizedCode.startsWith("execution_sync_status")) {
      return false;
    }
    const status = normalizedCode.split(":")[1] ?? "UNKNOWN";
    return costBreakdownStatusTone(status) === "danger";
  });
  if (profitabilityCostHasNoDataStatus(cost.status)) {
    if (hasBlockingSlippageStatus || hasBlockingExecutionStatus) {
      return "danger";
    }
    return hasWarnings || hasSlippageGap ? "warn" : "neutral";
  }
  if (hasBlockingSlippageStatus || hasBlockingExecutionStatus) {
    return "danger";
  }
  if (warningCodes.includes("positive_gross_negative_net") || (cost.net_pnl ?? 0) < 0) {
    return "danger";
  }
  return hasWarnings || hasSlippageGap ? "warn" : "safe";
}

const profitabilityCostWarningCopy: Record<string, string> = {
  fee_exceeds_gross_pnl: "수수료가 총손익보다 큽니다.",
  cost_exceeds_gross_pnl: "수수료와 펀딩비가 총손익보다 큽니다.",
  positive_gross_negative_net: "총손익은 양수지만 비용 반영 후 순손익은 음수입니다.",
  adverse_slippage_positive: "평균 체결 불리도가 거래자에게 불리하게 누적되고 있습니다.",
  "funding_sync_status:STALE": "펀딩비 동기화가 오래되어 비용 합계를 확정할 수 없습니다.",
  "funding_sync_status:INCOMPLETE": "펀딩비 동기화가 불완전해 비용 합계를 확정할 수 없습니다.",
  "funding_sync_status:UNKNOWN": "펀딩비 동기화 상태를 확인할 수 없습니다.",
  "slippage_data_status:NO_SAMPLE": "슬리피지 체결 표본이 없어 평균 체결 불리도를 확정할 수 없습니다.",
  "slippage_data_status:INCOMPLETE": "일부 체결에 슬리피지 값이 없어 평균 체결 불리도가 불완전합니다.",
  "slippage_data_status:UNKNOWN": "슬리피지 데이터 상태를 확인할 수 없습니다.",
  "slippage_data_status:NOT_READY": "슬리피지 준비 상태가 부족해 평균 체결 불리도를 확정할 수 없습니다.",
  "slippage_data_status:BLOCKED": "슬리피지 산출이 차단되어 평균 체결 불리도를 확정할 수 없습니다.",
  high_marketable_ratio_low_net_pnl: "즉시체결로 진입한 비중이 높고 순손익이 낮습니다.",
};

function normalizeProfitabilityWarningCode(code: string) {
  const trimmed = code.trim();
  const [prefix, value] = trimmed.split(":", 2);
  const normalizedPrefix = prefix.toLowerCase();
  if (
    (normalizedPrefix === "execution_sync_status" ||
      normalizedPrefix === "slippage_data_status" ||
      normalizedPrefix === "funding_sync_status") &&
    value
  ) {
    return `${normalizedPrefix}:${value.trim().toUpperCase()}`;
  }
  if (
    normalizedPrefix === "fee_asset_conversion_unavailable" ||
    normalizedPrefix === "funding_asset_conversion_unavailable"
  ) {
    const normalizedAsset = value?.trim().toUpperCase();
    return normalizedAsset ? `${normalizedPrefix}:${normalizedAsset}` : normalizedPrefix;
  }
  return trimmed;
}

function appendNormalizedWarningCode(warningCodes: string[], code: string) {
  const normalizedCode = normalizeProfitabilityWarningCode(code);
  if (!normalizedCode) {
    return;
  }
  const [prefix, value] = normalizedCode.split(":", 2);
  const statusPrefix =
    prefix === "execution_sync_status" ||
    prefix === "slippage_data_status" ||
    prefix === "funding_sync_status";
  if (statusPrefix && value) {
    if (value !== "UNKNOWN") {
      const unknownIndex = warningCodes.indexOf(`${prefix}:UNKNOWN`);
      if (unknownIndex >= 0) {
        warningCodes.splice(unknownIndex, 1);
      }
    } else if (warningCodes.some((warningCode) => warningCode.startsWith(`${prefix}:`) && warningCode !== normalizedCode)) {
      return;
    }
  }
  if (!warningCodes.includes(normalizedCode)) {
    warningCodes.push(normalizedCode);
  }
}

export function profitabilityCostWarningLabel(code: string) {
  const normalizedCode = normalizeProfitabilityWarningCode(code);
  if (normalizedCode.startsWith("fee_asset_conversion_unavailable")) {
    const asset = normalizedCode.split(":")[1]?.trim();
    return asset
      ? `${asset} 수수료를 USDT로 환산하지 못해 비용 합계에서 제외했습니다.`
      : "USDT로 환산하지 못한 수수료 자산이 있어 비용 합계에서 제외했습니다.";
  }
  if (normalizedCode.startsWith("funding_asset_conversion_unavailable")) {
    const asset = normalizedCode.split(":")[1]?.trim();
    return asset
      ? `${asset} 펀딩비를 USDT로 환산하지 못해 비용 합계에서 제외했습니다.`
      : "USDT로 환산하지 못한 펀딩비 자산이 있어 비용 합계에서 제외했습니다.";
  }
  if (normalizedCode.startsWith("funding_sync_status")) {
    return profitabilityCostWarningCopy[normalizedCode] ?? "펀딩비 동기화 상태를 확인해야 비용 합계를 확정할 수 있습니다.";
  }
  if (normalizedCode.startsWith("execution_sync_status")) {
    return profitabilityCostWarningCopy[normalizedCode] ?? "체결 동기화 상태를 확인해야 실현 손익을 확정할 수 있습니다.";
  }
  if (normalizedCode.startsWith("slippage_data_status")) {
    return (
      profitabilityCostWarningCopy[normalizedCode] ??
      "슬리피지 데이터 상태를 확인해야 평균 체결 불리도를 확정할 수 있습니다."
    );
  }
  return profitabilityCostWarningCopy[normalizedCode] ?? code;
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
  const badges: Array<{ label: string; tone: CostBreakdownTone }> = [];

  if (dataQuality.realized_pnl_confirmed) {
    badges.push({ label: "실현 손익 확정", tone: "good" });
  } else {
    badges.push({ label: "실현 손익 미확정", tone: "danger" });
  }

  if (dataQuality.missing_close_execution_count > 0) {
    badges.push({ label: `청산 체결 누락 ${dataQuality.missing_close_execution_count}건`, tone: "danger" });
  }

  if (normalizeStatusCode(dataQuality.execution_sync_status) !== "COMPLETE") {
    badges.push({
      label: `체결 동기화 ${statusLabel(dataQuality.execution_sync_status)}`,
      tone: costBreakdownStatusTone(dataQuality.execution_sync_status),
    });
  }

  if (normalizeStatusCode(dataQuality.funding_sync_status) !== "COMPLETE") {
    badges.push({
      label: `펀딩 동기화 ${statusLabel(dataQuality.funding_sync_status)}`,
      tone: costBreakdownStatusTone(dataQuality.funding_sync_status),
    });
  }

  const slippageStatus = slippageDataStatusFromQuality(dataQuality);
  if (slippageStatus !== "COMPLETE") {
    badges.push({
      label: slippageDataQualityLabel(dataQuality),
      tone: slippageDataQualityTone(slippageStatus),
    });
  }

  return badges;
}

function costBreakdownWarningCodes(payload: AnalyticsCostBreakdownResponse) {
  const warnings: string[] = [];
  const appendWarning = (warning: string) => {
    appendNormalizedWarningCode(warnings, warning);
  };

  for (const warning of payload.warnings ?? []) {
    appendWarning(warning);
  }
  for (const warning of payload.data_quality.warning_codes ?? []) {
    appendWarning(warning);
  }
  for (const bucket of payload.buckets ?? []) {
    for (const warning of bucket.warning_codes ?? []) {
      appendWarning(warning);
    }
    const bucketSlippageStatus = costBreakdownBucketSlippageStatus(bucket, payload.data_quality);
    if (hasBucketSlippagePublication(bucket) && bucketSlippageStatus !== "COMPLETE") {
      appendWarning(`slippage_data_status:${bucketSlippageStatus}`);
    }
  }
  const fundingStatus = payload.data_quality.funding_sync_status?.trim().toUpperCase();
  if (fundingStatus && fundingStatus !== "COMPLETE") {
    appendWarning(`funding_sync_status:${fundingStatus}`);
  }
  const executionStatus = payload.data_quality.execution_sync_status?.trim().toUpperCase();
  if (executionStatus && executionStatus !== "COMPLETE") {
    appendWarning(`execution_sync_status:${executionStatus}`);
  }
  const missingCloseExecutionCount = payload.data_quality.missing_close_execution_count;
  if (Number.isFinite(missingCloseExecutionCount) && missingCloseExecutionCount > 0) {
    appendWarning(`missing_close_execution_count:${missingCloseExecutionCount}`);
  }
  const slippageStatus = slippageDataStatusFromQuality(payload.data_quality);
  if (slippageStatus !== "COMPLETE") {
    appendWarning(`slippage_data_status:${slippageStatus}`);
  }

  return warnings;
}

function isCostBreakdownDangerWarning(warning: string) {
  const normalizedWarning = normalizeProfitabilityWarningCode(warning);
  const lowerWarning = normalizedWarning.toLowerCase();
  if (lowerWarning.startsWith("missing_close_execution_count")) {
    return true;
  }
  if (lowerWarning.startsWith("slippage_data_status")) {
    const status = normalizedWarning.split(":")[1] ?? "UNKNOWN";
    return slippageDataQualityTone(status) === "danger";
  }
  if (lowerWarning.startsWith("execution_sync_status")) {
    const status = normalizedWarning.split(":")[1] ?? "UNKNOWN";
    return costBreakdownStatusTone(status) === "danger";
  }
  return false;
}

export function costBreakdownWarningTone(payload: AnalyticsCostBreakdownResponse): "good" | "warn" | "danger" {
  const warnings = costBreakdownWarningCodes(payload);
  if (warnings.some(isCostBreakdownDangerWarning)) {
    return "danger";
  }
  if (typeof payload.summary.fee_ratio_pct === "number" && payload.summary.fee_ratio_pct >= 15) {
    return "warn";
  }
  return warnings.length > 0 ? "warn" : "good";
}

export function costBreakdownWarningMessages(payload: AnalyticsCostBreakdownResponse) {
  const warnings = costBreakdownWarningCodes(payload);

  const messages: string[] = warnings.map((warning) => {
    const normalizedWarning = normalizeProfitabilityWarningCode(warning);
    const lowerWarning = normalizedWarning.toLowerCase();
    if (lowerWarning.startsWith("missing_close_execution_count")) {
      return "청산 체결 누락으로 실현 손익이 확정되지 않았습니다.";
    }
    if (lowerWarning.startsWith("funding_sync_status")) {
      return profitabilityCostWarningLabel(normalizedWarning);
    }
    if (lowerWarning.startsWith("execution_sync_status")) {
      return profitabilityCostWarningLabel(normalizedWarning);
    }
    if (lowerWarning.startsWith("slippage_data_status")) {
      return profitabilityCostWarningLabel(normalizedWarning);
    }
    if (
      lowerWarning.startsWith("fee_asset_conversion_unavailable") ||
      lowerWarning.startsWith("funding_asset_conversion_unavailable")
    ) {
      return profitabilityCostWarningLabel(normalizedWarning);
    }
    if (lowerWarning.startsWith("fee_asset_unconverted")) {
      const asset = normalizedWarning.split(":")[1]?.trim();
      return asset
        ? `${asset} 수수료를 USDT로 환산하지 못했습니다.`
        : "USDT로 환산하지 못한 수수료 자산이 있습니다.";
    }
    if (lowerWarning.startsWith("funding_asset_unconverted")) {
      const asset = normalizedWarning.split(":")[1]?.trim();
      return asset
        ? `${asset} 펀딩비를 USDT로 환산하지 못했습니다.`
        : "USDT로 환산하지 못한 펀딩비 자산이 있습니다.";
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
  if (normalizeStatusCode(dataQuality.funding_sync_status) !== "COMPLETE") {
    statuses.push("펀딩비 미확정");
  }
  const bucketSlippageStatus = costBreakdownBucketSlippageStatus(bucket, dataQuality);
  if (bucketSlippageStatus !== "COMPLETE") {
    const slippageContext = hasBucketSlippagePublication(bucket)
      ? { ...bucket, slippage_data_status: bucketSlippageStatus }
      : dataQuality;
    statuses.push(slippageDataQualityLabel(slippageContext));
  }
  if (bucket.gross_pnl_usdt <= 0 && (bucket.fee_usdt > 0 || bucket.total_cost_usdt > 0)) {
    statuses.push("비율 N/A");
  }
  return statuses.length > 0 ? statuses.join(" · ") : "정상";
}
