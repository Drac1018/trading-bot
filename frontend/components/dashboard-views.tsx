import Link from "next/link";

import type { OperatorDashboardPayload } from "./overview-dashboard";
import { DataTable } from "./data-table";
import { MarketChartAutoRefresh } from "./market-chart-auto-refresh";
import { MarketChartZoomShell } from "./market-chart-zoom-shell";
import type { BinanceChartCandle } from "../lib/binance-chart-candles";
import { getSelectedSymbolPolicyHint } from "../lib/selected-symbol";
import {
  describeAiTriggerReason,
  describeHistoricalDecisionGap,
  isHistoricalExecutionRecord,
  summarizeRecentExecutionRecord,
  summarizeCurrentCycleSelection,
  summarizeExecutionState,
  summarizeLastAiRecommendation,
  summarizeRiskGate,
} from "../lib/decision-timeline";
import {
  formatDisplayValue,
  formatMacroEventContextDetail,
  formatMacroEventContextSummary,
  type MacroEventContextSummary,
} from "../lib/ui-copy";

type Row = Record<string, unknown>;
type AiReviewReadModel = {
  review_type?: string | null;
  trigger_reason?: string | null;
  trigger_reason_codes?: string[] | null;
  skip_reason?: string | null;
  dedupe_reason?: string | null;
  trigger_deduped?: boolean | null;
  provider_status?: string | null;
  provider_invoked?: boolean | null;
  provider_skipped?: boolean | null;
  invoked_at?: string | null;
  provider_name?: string | null;
  trigger_fingerprint?: string | null;
};
type RiskCheckRow = {
  id?: number | null;
  symbol?: string | null;
  decision_run_id?: number | null;
  allowed?: boolean | null;
  decision?: string | null;
  reason_codes?: string[];
  approved_risk_pct?: number | null;
  approved_leverage?: number | null;
  ai_review?: AiReviewReadModel | null;
  ai_review_type?: string | null;
  ai_trigger_reason?: string | null;
  ai_trigger_reason_codes?: string[];
  ai_skip_reason?: string | null;
  last_ai_skip_reason?: string | null;
  ai_trigger_summary?: string | null;
  market_signal_summary?: string | null;
  macro_event_context_summary?: MacroEventContextSummary | null;
  macro_event_risk_summary?: MacroEventContextSummary | null;
  created_at?: string | null;
  payload?: Record<string, unknown> | null;
};

type OperatorSymbol = OperatorDashboardPayload["symbols"][number];
type OperatorAiDecisionReadModel = OperatorSymbol["ai_decision"] & {
  ai_review?: AiReviewReadModel | null;
  ai_review_type?: string | null;
  ai_trigger_reason_codes?: string[] | null;
  ai_skip_reason?: string | null;
  ai_trigger_summary?: string | null;
  market_signal_summary?: string | null;
  market_signal_context?: Record<string, unknown> | null;
  macro_event_context_summary?: MacroEventContextSummary | null;
  macro_event_risk_summary?: MacroEventContextSummary | null;
};
type OperatorProtectionReadModel = OperatorSymbol["protection_status"] & {
  blocked_reason_code?: string | null;
  blocked_reason?: string | null;
  verification_deadline_at?: string | null;
};
type TimelineStage = {
  key: string;
  title: string;
  label: string;
  detail: string;
  kind: "good" | "warn" | "danger" | "neutral";
};
type RiskReasonGroupKey = "freshness" | "exposure" | "approval" | "protection" | "trigger" | "other";
type CandleWindow = 120 | 240 | 500 | 1000;
export type MarketChartTimeframe = "15m" | "1h" | "4h";
export type MarketChartZoomRange = { start: number; end: number };
type MarketOverlayKey = "close" | "volume" | "levels" | "averageVolume";
type CandlePoint = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};
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
type MarketChartEventMarker = {
  timestamp: string;
  kind: "ai" | "risk" | "execution";
  label: string;
  detail: string;
};
type MarketChartModel = {
  symbol: string;
  window: CandleWindow;
  timeframe: MarketChartTimeframe;
  sourceTimeframe: string;
  sourceNote: string;
  snapshotTime: string | null;
  latestPrice: number | null;
  latestVolume: number | null;
  candles: CandlePoint[];
  baseCandleCount: number;
  visibleStartIndex: number;
  visibleEndIndex: number;
  feature: Row | null;
  isComplete: boolean | null;
  isStale: boolean | null;
  stats: MarketChartStats;
  rangeStats: MarketChartStats;
  rangeLabel: string;
  symbolState: OperatorSymbol;
  events: MarketChartEventMarker[];
};
type MarketChartStats = {
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
type MarketChartCandlesBySymbol = Record<string, BinanceChartCandle[]>;

const marketCandleWindowOptions: { value: CandleWindow; label: string }[] = [
  { value: 120, label: "120봉" },
  { value: 240, label: "240봉" },
  { value: 500, label: "500봉" },
  { value: 1000, label: "1000봉" },
];

const marketChartTimeframeOptions: { value: MarketChartTimeframe; label: string }[] = [
  { value: "15m", label: "15분" },
  { value: "1h", label: "1시간" },
  { value: "4h", label: "4시간" },
];

const defaultMarketOverlays: MarketOverlayKey[] = ["close", "volume", "levels", "averageVolume"];
const minMarketChartZoomCandles = 12;

const operatingStateLabelMap: Record<string, string> = {
  TRADABLE: "신규 진입 가능",
  PROTECTION_REQUIRED: "보호 복구 우선",
  DEGRADED_MANAGE_ONLY: "관리 전용",
  EMERGENCY_EXIT: "비상 청산",
  PAUSED: "일시 중지",
};

const reasonCodeLabelMap: Record<string, string> = {
  TRADING_PAUSED: "시스템 가드 모드",
  HOLD_DECISION: "현재는 신규 진입 신호가 없어 대기 중입니다.",
  LIVE_APPROVAL_REQUIRED: "실거래 승인 창 닫힘",
  LIVE_TRADING_DISABLED: "실거래 비활성화",
  PROTECTION_REQUIRED: "보호 주문 복구 필요",
  DEGRADED_MANAGE_ONLY: "관리 전용 상태",
  EMERGENCY_EXIT: "비상 청산 상태",
  MANUAL_USER_REQUEST: "수동 중지",
  PROTECTIVE_ORDER_FAILURE: "보호 주문 이상",
  ACCOUNT_STATE_STALE: "계좌 상태 stale",
  POSITION_STATE_STALE: "포지션 상태 stale",
  OPEN_ORDERS_STATE_STALE: "오더 상태 stale",
  PROTECTION_STATE_UNVERIFIED: "보호 주문 검증 불가",
  UNRESOLVED_SUBMISSION_GUARD_ACTIVE: "미해결 주문 제출 가드",
  UNRESOLVED_SUBMISSION_DEADLINE_EXCEEDED: "미해결 주문 확인 초과",
  LIVE_ORDER_SUBMISSION_UNKNOWN: "주문 제출 결과 불명확",
  BINANCE_REST_CIRCUIT_OPEN: "Binance REST 회로 열림",
  DRAWDOWN_STATE_CAUTION: "드로다운 주의",
  DRAWDOWN_STATE_CONTAINMENT: "드로다운 억제",
  DRAWDOWN_STATE_RECOVERY: "드로다운 회복",
  ENTRY_CANDIDATE_SELECTED: "신규 진입 후보 선정",
  ENTRY_CANDIDATE_WEAK_VOLUME_PREAI: "거래량 부족으로 AI 검토 생략",
  MACRO_EVENT_IMMINENT: "주요 경제 이벤트 임박으로 신규 진입 보수화",
  MACRO_EVENT_RISK_WINDOW_ACTIVE: "거시 이벤트 리스크 구간",
  MACRO_RELEASE_REACTION_WINDOW: "발표 직후 변동성 구간",
  STALE_MARKET_DATA: "시장 데이터 지연",
};

const schedulerStatusLabelMap: Record<string, string> = {
  running: "실행 중",
  success: "성공",
  failed: "실패",
};

const aiReviewTypeLabelMap: Record<string, string> = {
  entry_candidate_review: "신규 진입 후보 검토",
  breakout_exception_review: "돌파 예외 검토",
  protection_review: "보호 상태 점검",
  manual_review: "수동 검토",
  open_position_review: "오픈 포지션 점검",
  periodic_backstop_review: "주기 백스톱 검토",
};

const triggerReasonReviewLabelMap: Record<string, string> = {
  entry_candidate_event: "신규 진입 후보 검토",
  breakout_exception_event: "돌파 예외 검토",
  protection_review_event: "보호 상태 점검",
  manual_review_event: "수동 검토",
  open_position_recheck_due: "오픈 포지션 점검",
  periodic_backstop_due: "주기 백스톱 검토",
};

const riskReasonGroupLabels: Record<RiskReasonGroupKey, string> = {
  freshness: "데이터 신선도",
  exposure: "노출 한도",
  approval: "승인 상태",
  protection: "보호 주문",
  trigger: "진입 조건",
  other: "기타",
};

const riskReasonGroupHints: Record<RiskReasonGroupKey, string> = {
  freshness: "계좌, 포지션, 오더, 시장 데이터가 stale/incomplete/untrusted 상태인지 확인",
  exposure: "단일 포지션, 방향 편중, 총 노출, 동일 tier 집중도, 상관 위험 한도",
  approval: "live approval window, live arm/disarm, 실거래 승인 상태",
  protection: "보호 주문 누락, 미검증, stop/take profit 확인 실패",
  trigger: "pending entry plan 또는 진입 트리거 조건 미충족",
  other: "아직 별도 운영 그룹에 매핑되지 않은 원문 사유",
};

const riskReasonGroupOrder: RiskReasonGroupKey[] = [
  "freshness",
  "exposure",
  "approval",
  "protection",
  "trigger",
  "other",
];

const passiveEntryReasonCodes = new Set([
  "HOLD_DECISION",
  "ENTRY_TRIGGER_NOT_MET",
  "NO_EDGE",
  "RANGE_CHOP",
  "WEAK_VOLUME",
  "MOMENTUM_WEAKENING",
]);

function asNonEmptyString(value: unknown) {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function asStringArray(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function isPassiveEntryReason(value: string | null | undefined) {
  return value ? passiveEntryReasonCodes.has(value.trim().toUpperCase()) : false;
}

function hasOnlyPassiveEntryReasons(values: string[] | null | undefined) {
  const reasons = values?.filter((value) => value.trim().length > 0) ?? [];
  return reasons.length > 0 && reasons.every(isPassiveEntryReason);
}

function asMacroEventContextSummary(value: unknown): MacroEventContextSummary | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as MacroEventContextSummary) : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

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

function rowString(row: Row | null | undefined, key: string) {
  const value = row?.[key];
  return typeof value === "string" ? value : null;
}

function rowNumber(row: Row | null | undefined, key: string) {
  return asFiniteNumber(row?.[key]);
}

function rowBoolean(row: Row | null | undefined, key: string) {
  const value = row?.[key];
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    return value === 1 ? true : value === 0 ? false : null;
  }
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (normalized === "true" || normalized === "1") {
      return true;
    }
    if (normalized === "false" || normalized === "0") {
      return false;
    }
  }
  return null;
}

function asAiReviewReadModel(value: unknown): AiReviewReadModel | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as AiReviewReadModel) : null;
}

function aiDecisionReadModel(symbol: OperatorSymbol): OperatorAiDecisionReadModel {
  return symbol.ai_decision as OperatorAiDecisionReadModel;
}

function aiReviewReadModel(symbol: OperatorSymbol): AiReviewReadModel | null {
  return asAiReviewReadModel(aiDecisionReadModel(symbol).ai_review);
}

function protectionReadModel(symbol: OperatorSymbol): OperatorProtectionReadModel {
  return symbol.protection_status as OperatorProtectionReadModel;
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
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function translateMarketInputFlag(value: string) {
  const labels: Record<string, string> = {
    account: "계좌 stale",
    positions: "포지션 stale",
    open_orders: "오더 stale",
    protective_orders: "보호 주문 stale",
    market_snapshot: "시장 스냅샷 stale",
    market_snapshot_incomplete: "시장 스냅샷 불완전",
    feature_input_missing: "지표 입력 없음",
  };
  return labels[value] ?? value;
}

function formatMarketTiming(symbol: OperatorDashboardPayload["symbols"][number]) {
  const parts: string[] = [];
  if (symbol.market_candle_time) {
    parts.push(`캔들 ${formatDateTime(symbol.market_candle_time)}`);
  }
  if (symbol.market_snapshot_time) {
    parts.push(`수집 ${formatDateTime(symbol.market_snapshot_time)}`);
  }
  return `${parts.join(" / ") || "기록 없음"} / 시장 ${symbol.timeframe ?? "-"}`;
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

function formatRatio(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${(value * 100).toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}%`;
}

function badgeClass(kind: "good" | "warn" | "danger" | "neutral") {
  return {
    good: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warn: "border-amber-200 bg-amber-50 text-amber-800",
    danger: "border-rose-200 bg-rose-50 text-rose-800",
    neutral: "border-slate-200 bg-slate-50 text-slate-700",
  }[kind];
}

function metricCard(title: string, value: string, hint: string, options?: { compact?: boolean }) {
  const compact = options?.compact ?? false;
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4">
      <p className="text-xs font-medium text-slate-500">{title}</p>
      <p
        className={`mt-2 font-semibold text-slate-950 ${
          compact ? "text-base leading-6 break-all sm:text-lg" : "text-xl leading-7 break-words"
        }`}
      >
        {value}
      </p>
      <p className="mt-2 break-words text-xs leading-5 text-slate-500">{hint}</p>
    </div>
  );
}

function marketSnapshotCandles(row: Row | null): CandlePoint[] {
  const payload = asRecord(row?.payload);
  const candles = Array.isArray(payload?.candles) ? payload.candles : [];
  return candles
    .map((item): CandlePoint | null => {
      const record = asRecord(item);
      const timestamp = typeof record?.timestamp === "string" ? record.timestamp : null;
      const open = asFiniteNumber(record?.open);
      const high = asFiniteNumber(record?.high);
      const low = asFiniteNumber(record?.low);
      const close = asFiniteNumber(record?.close);
      const volume = asFiniteNumber(record?.volume);
      if (!timestamp || open === null || high === null || low === null || close === null || volume === null) {
        return null;
      }
      return { timestamp, open, high, low, close, volume };
    })
    .filter((item): item is CandlePoint => item !== null);
}

function rowTimestampMs(row: Row, keys: string[]) {
  for (const key of keys) {
    const value = rowString(row, key);
    if (!value) {
      continue;
    }
    const parsed = Date.parse(value.endsWith("Z") ? value : `${value}Z`);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }
  return 0;
}

function latestRow(rows: Row[], keys: string[]) {
  return rows.reduce<Row | null>((latest, row) => {
    if (latest === null) {
      return row;
    }
    return rowTimestampMs(row, keys) > rowTimestampMs(latest, keys) ? row : latest;
  }, null);
}

function applyMarketChartZoomRange(candles: CandlePoint[], range: MarketChartZoomRange | null | undefined) {
  const maxIndex = candles.length - 1;
  if (!range || maxIndex < minMarketChartZoomCandles) {
    return {
      candles,
      visibleStartIndex: 0,
      visibleEndIndex: Math.max(maxIndex, 0),
    };
  }

  let start = Math.max(0, Math.min(maxIndex, Math.trunc(range.start)));
  let end = Math.max(start, Math.min(maxIndex, Math.trunc(range.end)));
  if (end - start + 1 < minMarketChartZoomCandles) {
    const center = (start + end) / 2;
    start = Math.floor(center - (minMarketChartZoomCandles - 1) / 2);
    end = start + minMarketChartZoomCandles - 1;
    if (start < 0) {
      start = 0;
      end = Math.min(maxIndex, minMarketChartZoomCandles - 1);
    }
    if (end > maxIndex) {
      end = maxIndex;
      start = Math.max(0, maxIndex - minMarketChartZoomCandles + 1);
    }
  }

  if (start <= 0 && end >= maxIndex) {
    return {
      candles,
      visibleStartIndex: 0,
      visibleEndIndex: Math.max(maxIndex, 0),
    };
  }

  return {
    candles: candles.slice(start, end + 1),
    visibleStartIndex: start,
    visibleEndIndex: end,
  };
}

function latestSnapshotForSymbol(snapshots: Row[], symbol: string, timeframe: string) {
  return latestRow(
    snapshots.filter(
      (row) => rowString(row, "symbol")?.toUpperCase() === symbol && rowString(row, "timeframe") === timeframe,
    ),
    ["snapshot_time", "created_at"],
  );
}

function latestFeatureForSnapshot(features: Row[], symbol: string, snapshot: Row | null, timeframe: string) {
  const snapshotId = rowNumber(snapshot, "id");
  const candidates = features.filter(
    (row) => rowString(row, "symbol")?.toUpperCase() === symbol && rowString(row, "timeframe") === timeframe,
  );
  const exactMatch =
    snapshotId === null ? null : latestRow(candidates.filter((row) => rowNumber(row, "market_snapshot_id") === snapshotId), ["feature_time", "created_at"]);
  return exactMatch ?? latestRow(candidates, ["feature_time", "created_at"]);
}

function sourceCandlesForTimeframe(candles: CandlePoint[], timeframe: MarketChartTimeframe, sourceTimeframe: string) {
  if (timeframe === "15m" || sourceTimeframe !== "15m") {
    return candles;
  }
  const bucketSize = timeframe === "1h" ? 4 : 16;
  const bucketed: CandlePoint[] = [];
  const firstBucketSize = candles.length % bucketSize || bucketSize;
  for (let index = 0; index < candles.length; ) {
    const size = index === 0 ? firstBucketSize : bucketSize;
    const bucket = candles.slice(index, index + size);
    const first = bucket[0];
    const last = bucket[bucket.length - 1];
    if (first && last) {
      bucketed.push({
        timestamp: last.timestamp,
        open: first.open,
        high: Math.max(...bucket.map((item) => item.high)),
        low: Math.min(...bucket.map((item) => item.low)),
        close: last.close,
        volume: bucket.reduce((total, item) => total + item.volume, 0),
      });
    }
    index += size;
  }
  return bucketed;
}

function marketFeatureForTimeframe(feature: Row | null, timeframe: MarketChartTimeframe) {
  if (!feature || timeframe === rowString(feature, "timeframe")) {
    return feature;
  }
  const payload = asRecord(feature.payload);
  const multiTimeframe = asRecord(payload?.multi_timeframe);
  const timeframePayload = asRecord(multiTimeframe?.[timeframe]);
  if (!timeframePayload) {
    return feature;
  }
  return {
    ...feature,
    timeframe,
    trend_score: asFiniteNumber(timeframePayload.trend_score),
    volatility_pct: asFiniteNumber(timeframePayload.volatility_pct),
    volume_ratio: asFiniteNumber(timeframePayload.volume_ratio),
    drawdown_pct: asFiniteNumber(timeframePayload.drawdown_pct),
    rsi: asFiniteNumber(timeframePayload.rsi),
    atr: asFiniteNumber(timeframePayload.atr),
    payload: {
      ...(payload ?? {}),
      ...timeframePayload,
      timeframe,
    },
  };
}

function availableMarketTimeframes(snapshots: Row[], features: Row[]) {
  const snapshotTimeframes = new Set(snapshots.map((row) => rowString(row, "timeframe")).filter(Boolean));
  const has15mCandles = snapshots.some((row) => rowString(row, "timeframe") === "15m" && marketSnapshotCandles(row).length > 0);
  const featureTimeframes = new Set<string>();
  for (const feature of features) {
    const direct = rowString(feature, "timeframe");
    if (direct) {
      featureTimeframes.add(direct);
    }
    const payload = asRecord(feature.payload);
    const multiTimeframe = asRecord(payload?.multi_timeframe);
    for (const key of Object.keys(multiTimeframe ?? {})) {
      featureTimeframes.add(key);
    }
  }
  return new Map(
    marketChartTimeframeOptions.map((option) => {
      const directSnapshot = snapshotTimeframes.has(option.value);
      const enabled = option.value === "15m" ? directSnapshot : has15mCandles && featureTimeframes.has(option.value);
      const detail = directSnapshot
        ? "OHLC 직접 수집"
        : enabled
          ? "15분 캔들 집계 + 다중 타임프레임 지표"
          : "현재 데이터 없음";
      return [option.value, { enabled, detail }] as const;
    }),
  );
}

function buildMarketChartEventMarkers(symbol: OperatorSymbol): MarketChartEventMarker[] {
  const markers: MarketChartEventMarker[] = [];
  const aiAt = symbol.ai_decision.last_ai_invoked_at ?? symbol.ai_decision.created_at;
  if (aiAt) {
    markers.push({
      timestamp: aiAt,
      kind: "ai",
      label: "AI",
      detail: `${translateDecision(symbol.ai_decision.decision)} / ${aiReviewTypeLabel(symbol)}`,
    });
  }
  if (symbol.risk_guard.allowed === false && symbol.risk_guard.created_at) {
    markers.push({
      timestamp: symbol.risk_guard.created_at,
      kind: "risk",
      label: "위험",
      detail:
        symbol.risk_guard.blocked_reason_codes.length > 0
          ? formatTranslatedCodeList(symbol.risk_guard.blocked_reason_codes)
          : "리스크 차단",
    });
  }
  if (symbol.execution.order_id && (symbol.execution.execution_created_at ?? symbol.execution.created_at)) {
    markers.push({
      timestamp: symbol.execution.execution_created_at ?? symbol.execution.created_at ?? "",
      kind: "execution",
      label: "주문",
      detail: symbol.execution.execution_status ?? symbol.execution.order_status ?? "주문 기록",
    });
  }
  return markers.filter((marker) => marker.timestamp.length > 0);
}

function buildMarketChartModels(
  symbols: OperatorDashboardPayload["symbols"],
  snapshots: Row[],
  features: Row[],
  candleWindow: CandleWindow,
  timeframe: MarketChartTimeframe,
  chartCandlesBySymbol: MarketChartCandlesBySymbol = {},
  chartZoomRange: MarketChartZoomRange | null = null,
): MarketChartModel[] {
  return symbols
    .map((symbol): MarketChartModel | null => {
      const snapshot = latestSnapshotForSymbol(snapshots, symbol.symbol, timeframe) ?? latestSnapshotForSymbol(snapshots, symbol.symbol, "15m");
      if (!snapshot) {
        return null;
      }
      const latestVolume = rowNumber(snapshot, "latest_volume");
      const directChartCandles = chartCandlesBySymbol[symbol.symbol.toUpperCase()] ?? [];
      const hasDirectChartCandles = directChartCandles.length > 0;
      const sourceTimeframe = hasDirectChartCandles ? timeframe : (rowString(snapshot, "timeframe") ?? "15m");
      const sourceCandles = hasDirectChartCandles ? directChartCandles : marketSnapshotCandles(snapshot);
      const timeframeCandles = hasDirectChartCandles
        ? sourceCandles
        : sourceCandlesForTimeframe(sourceCandles, timeframe, sourceTimeframe);
      const baseCandles = timeframeCandles.slice(-candleWindow);
      const zoomedCandles = applyMarketChartZoomRange(baseCandles, chartZoomRange);
      const candles = zoomedCandles.candles;
      const latestCandle = candles[candles.length - 1];
      const feature =
        marketFeatureForTimeframe(latestFeatureForSnapshot(features, symbol.symbol, snapshot, timeframe), timeframe) ??
        marketFeatureForTimeframe(latestFeatureForSnapshot(features, symbol.symbol, snapshot, "15m"), timeframe);
      const rangeCandles = sourceCandles.slice(-Math.min(60, sourceCandles.length));
      return {
        symbol: symbol.symbol,
        window: candleWindow,
        timeframe,
        sourceTimeframe,
        sourceNote: hasDirectChartCandles ? "Binance 선물 직접 조회" : timeframe === sourceTimeframe ? "직접 수집" : `${sourceTimeframe} 캔들 집계`,
        snapshotTime: rowString(snapshot, "snapshot_time") ?? rowString(snapshot, "created_at"),
        latestPrice: latestCandle?.close ?? rowNumber(snapshot, "latest_price"),
        latestVolume: latestCandle?.volume ?? latestVolume,
        candles,
        baseCandleCount: baseCandles.length,
        visibleStartIndex: zoomedCandles.visibleStartIndex,
        visibleEndIndex: zoomedCandles.visibleEndIndex,
        feature,
        isComplete: rowBoolean(snapshot, "is_complete"),
        isStale: rowBoolean(snapshot, "is_stale"),
        stats: marketChartStats({ candles, latestVolume }),
        rangeStats: marketChartStats({ candles: rangeCandles, latestVolume }),
        rangeLabel: `최근 ${rangeCandles.length}개 ${sourceTimeframe}`,
        symbolState: symbol,
        events: buildMarketChartEventMarkers(symbol),
      };
    })
    .filter((item): item is MarketChartModel => item !== null);
}

function marketPriceDigits(value: number | null | undefined) {
  if (value === null || value === undefined || value >= 100) {
    return 2;
  }
  return value >= 1 ? 4 : 5;
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

function marketCandleWindowLabel(value: CandleWindow, _timeframe: MarketChartTimeframe = "15m") {
  return marketCandleWindowOptions.find((option) => option.value === value)?.label ?? `${value}봉`;
}

function marketTimeframeLabel(value: MarketChartTimeframe) {
  return marketChartTimeframeOptions.find((option) => option.value === value)?.label ?? value;
}

function marketVisibleCandleWindowLabel(model: MarketChartModel) {
  if (model.baseCandleCount > 0 && model.candles.length < model.baseCandleCount) {
    return `${model.candles.length}봉 확대`;
  }
  return marketCandleWindowLabel(model.window, model.timeframe);
}

function marketVisibleCandleWindowHint(model: MarketChartModel) {
  if (model.baseCandleCount > 0 && model.candles.length < model.baseCandleCount) {
    return `전체 ${model.baseCandleCount}봉 중 ${model.visibleStartIndex + 1}-${model.visibleEndIndex + 1}봉`;
  }
  return `${model.timeframe} 표시 / ${model.sourceNote}`;
}

function marketChartStats(model: Pick<MarketChartModel, "candles" | "latestVolume">): MarketChartStats {
  const candles = model.candles;
  if (candles.length === 0) {
    return {
      firstClose: null,
      lastClose: null,
      high: null,
      low: null,
      changePct: null,
      rangePct: null,
      rangePositionPct: null,
      averageVolume: null,
      volumeVsAverage: null,
    };
  }
  const firstClose = candles[0]?.close ?? null;
  const lastClose = candles[candles.length - 1]?.close ?? null;
  const high = Math.max(...candles.map((item) => item.high));
  const low = Math.min(...candles.map((item) => item.low));
  const averageVolume = candles.reduce((total, item) => total + item.volume, 0) / candles.length;
  const rangePositionPct =
    lastClose !== null && high > low ? Math.max(0, Math.min(100, ((lastClose - low) / (high - low)) * 100)) : null;
  return {
    firstClose,
    lastClose,
    high,
    low,
    changePct: firstClose && lastClose !== null ? ((lastClose - firstClose) / firstClose) * 100 : null,
    rangePct: firstClose ? ((high - low) / firstClose) * 100 : null,
    rangePositionPct,
    averageVolume,
    volumeVsAverage: averageVolume > 0 && model.latestVolume !== null ? model.latestVolume / averageVolume : null,
  };
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

function marketIndicatorCell(label: string, value: string, hint: string) {
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-3">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold text-slate-950">{value}</p>
      <p className="mt-1 text-xs leading-5 text-slate-500">{hint}</p>
    </div>
  );
}

function MarketChartStatsStrip({ model }: { model: MarketChartModel }) {
  const stats = model.stats;
  const priceDigits = marketPriceDigits(model.latestPrice);
  const changeTone = marketTone(stats.changePct);
  const candleLabel = marketVisibleCandleWindowLabel(model);
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      <div className={`rounded-md border px-3 py-3 ${badgeClass(changeTone)}`}>
        <p className="text-xs font-medium opacity-80">{candleLabel} 변화</p>
        <p className="mt-1 text-lg font-semibold">{formatPercentPoint(stats.changePct, 2)}</p>
        <p className="mt-1 text-xs opacity-80">
          {formatNumber(stats.firstClose, priceDigits)} → {formatNumber(stats.lastClose, priceDigits)}
        </p>
      </div>
      {marketIndicatorCell(
        `${candleLabel} 범위`,
        `${formatNumber(stats.low, priceDigits)} ~ ${formatNumber(stats.high, priceDigits)}`,
        `폭 ${formatPercentPoint(stats.rangePct, 2)} / 현재 ${formatNumber(model.rangeStats.rangePositionPct, 0)}%`,
      )}
      {marketIndicatorCell(
        "거래량 배율",
        stats.volumeVsAverage === null ? "-" : `${formatNumber(stats.volumeVsAverage, 2)}배`,
        `평균 ${formatNumber(stats.averageVolume, 2)}`,
      )}
      {marketIndicatorCell("캔들 수", `${model.candles.length}개`, marketVisibleCandleWindowHint(model))}
    </div>
  );
}

function buildCandleIndicatorSeries(candles: CandlePoint[], period = 14): CandleIndicatorPoint[] {
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

function buildBollingerSeries(candles: CandlePoint[], period = 20, multiplier = 2): BollingerPoint[] {
  return candles.map((candle, index) => {
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

function buildVolumeProfile(candles: CandlePoint[], binCount = 28): VolumeProfileBin[] {
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

function featureVolumeProfile(model: MarketChartModel) {
  const payload = asRecord(model.feature?.payload);
  return asRecord(payload?.volume_profile);
}

function planVolumeProfileDetails(symbol: OperatorSymbol) {
  const plan = asRecord(symbol.pending_entry_plan);
  const triggerDetails = asRecord(plan?.trigger_details);
  return asRecord(triggerDetails?.volume_profile);
}

function formatVolumeProfilePosition(value: string | null | undefined) {
  const labels: Record<string, string> = {
    below_value_area: "가치구간 하단 이탈",
    above_value_area: "가치구간 상단 돌파",
    above_poc_inside_value_area: "최대 거래 매물대 위 / 가치구간 내부",
    below_poc_inside_value_area: "최대 거래 매물대 아래 / 가치구간 내부",
    flat_profile: "단일 가격대 집중",
  };
  return value ? labels[value] ?? formatInternalCodeLabel(value) : "-";
}

function volumeProfileSummaryRows(
  value: Record<string, unknown> | null,
  priceDigits: number,
): [string, string][] {
  if (!value || value.available === false) {
    return [];
  }
  const poc = asFiniteNumber(value.poc_price);
  const valueAreaLow = asFiniteNumber(value.value_area_low);
  const valueAreaHigh = asFiniteNumber(value.value_area_high);
  const nearestSupport = asFiniteNumber(value.nearest_support);
  const nearestResistance = asFiniteNumber(value.nearest_resistance);
  const supportDistance = asFiniteNumber(value.support_distance_pct);
  const resistanceDistance = asFiniteNumber(value.resistance_distance_pct);
  return [
    ["최대 거래 매물대", formatNumber(poc, priceDigits)],
    ["가치구간", priceRangeText(valueAreaLow, valueAreaHigh, priceDigits)],
    [
      "지지/저항",
      `${formatNumber(nearestSupport, priceDigits)} / ${formatNumber(nearestResistance, priceDigits)}`,
    ],
    [
      "거리",
      `${formatUnsignedPercent(supportDistance, 2)} / ${formatUnsignedPercent(resistanceDistance, 2)}`,
    ],
  ];
}

function volumeProfileTooltipRows(value: Record<string, unknown> | null, priceDigits: number) {
  if (!value || value.available === false) {
    return [];
  }
  return [
    `AI 기준 매물대 위치: ${formatVolumeProfilePosition(asNonEmptyString(value.current_position))}`,
    `AI 최대 거래 매물대: ${formatNumber(asFiniteNumber(value.poc_price), priceDigits)}`,
    `AI 지지/저항: ${formatNumber(asFiniteNumber(value.nearest_support), priceDigits)} / ${formatNumber(
      asFiniteNumber(value.nearest_resistance),
      priceDigits,
    )}`,
  ];
}

function priceRangeText(min: number | null, max: number | null, digits: number) {
  if (min !== null && max !== null) {
    return min === max ? formatNumber(min, digits) : `${formatNumber(min, digits)}~${formatNumber(max, digits)}`;
  }
  return formatNumber(min ?? max, digits);
}

function aiChartDecisionRows(symbol: OperatorSymbol, priceDigits: number) {
  const plan = asRecord(symbol.pending_entry_plan);
  const decision = symbol.risk_guard.decision ?? symbol.ai_decision.decision;
  const confidence = symbol.ai_decision.confidence;
  const side = asNonEmptyString(plan?.side) ?? decision;
  const entryMin = asFiniteNumber(plan?.entry_zone_min);
  const entryMax = asFiniteNumber(plan?.entry_zone_max);
  const stopLoss = asFiniteNumber(plan?.stop_loss) ?? asFiniteNumber(plan?.invalidation_price);
  const takeProfit = asFiniteNumber(plan?.take_profit);
  const planStatus = asNonEmptyString(plan?.plan_status);
  const entryMode = asNonEmptyString(plan?.entry_mode);
  const rows = [`AI 판단: ${translateDecision(side)} / 신뢰도 ${formatRatio(confidence)}`];

  if (planStatus) {
    rows.push(`계획: ${formatInternalCodeLabel(planStatus)}${entryMode ? ` / ${formatInternalCodeLabel(entryMode)}` : ""}`);
  }
  if (entryMin !== null || entryMax !== null) {
    rows.push(`진입: ${priceRangeText(entryMin, entryMax, priceDigits)}`);
  }
  if (takeProfit !== null || stopLoss !== null) {
    rows.push(`목표/손절: ${formatNumber(takeProfit, priceDigits)} / ${formatNumber(stopLoss, priceDigits)}`);
  }
  if (rows.length === 1 && symbol.ai_decision.explanation_short) {
    rows.push(`요약: ${symbol.ai_decision.explanation_short.slice(0, 34)}`);
  }
  return rows;
}

function candleTooltipRows(
  candle: CandlePoint,
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

function marketEventMarkerTooltipLabel(kind: MarketChartEventMarker["kind"]) {
  if (kind === "risk") {
    return "리스크";
  }
  if (kind === "execution") {
    return "주문";
  }
  return "AI 판단";
}

function MarketCandlestickSvg({
  model,
  compact = false,
}: {
  model: MarketChartModel;
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
  const backendVolumeProfile = featureVolumeProfile(model);
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
    risk: { fill: "#e11d48", stroke: "#fecdd3" },
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
    rows.push(`${marketEventMarkerTooltipLabel(marker.kind)}: ${marker.detail}`);
    markerRowsByIndex.set(marker.index, rows);
  });
  const aiRows = aiChartDecisionRows(model.symbolState, priceDigits);
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
    const showAiRows = index === latestIndex || aiMarkerIndexes.has(index) || markerRows.some((row) => row.startsWith("AI 판단:"));
    const tooltipExtras = {
      bollinger: bollingerSeries[index] ?? null,
      profileRows,
      markerRows,
      aiRows: showAiRows
        ? [
            ...aiRows,
            ...volumeProfileTooltipRows(planVolumeProfileDetails(model.symbolState) ?? backendVolumeProfile, priceDigits),
          ]
        : [],
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
          const y = priceTop + 10 + marker.slot * 16;
          const markerWidth = marker.label.length > 2 ? 30 : 22;
          return (
            <g key={`${marker.kind}-${marker.timestamp}-${marker.slot}`}>
              <title>{`${marker.label}: ${marker.detail} / ${formatDateTime(marker.timestamp)}`}</title>
              <line x1={marker.x} x2={marker.x} y1={priceTop} y2={priceTop + priceHeight} stroke={colors.fill} strokeDasharray="2 4" strokeWidth="1" opacity="0.55" />
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

function formatUnsignedPercent(value: number | null | undefined, digits = 2) {
  return value === null || value === undefined ? "-" : `${formatNumber(value, digits)}%`;
}

function marketChartHref({
  selectedSymbol,
  candleWindow,
  timeframe,
}: {
  selectedSymbol: string;
  candleWindow: CandleWindow;
  timeframe: MarketChartTimeframe;
}) {
  const params = new URLSearchParams();
  if (selectedSymbol !== "ALL") {
    params.set("symbol", selectedSymbol);
  }
  params.set("candles", String(candleWindow));
  params.set("timeframe", timeframe);
  return `/dashboard/market?${params.toString()}`;
}

function MarketChartStatusBadges({ model }: { model: MarketChartModel }) {
  const completeness =
    model.isComplete === null
      ? { label: "완전성 미확인", kind: "neutral" as const }
      : model.isComplete
        ? { label: "완전", kind: "good" as const }
        : { label: "부분", kind: "warn" as const };
  const freshness =
    model.isStale === null
      ? { label: "지연 미확인", kind: "neutral" as const }
      : model.isStale
        ? { label: "지연", kind: "warn" as const }
        : { label: "정상", kind: "good" as const };
  const badges = [
    { label: marketTimeframeLabel(model.timeframe), kind: "neutral" as const },
    { label: marketVisibleCandleWindowLabel(model), kind: "neutral" as const },
    completeness,
    freshness,
  ];
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {badges.map((badge) => (
        <span key={badge.label} className={`rounded-md border px-2 py-1 text-[11px] font-semibold ${badgeClass(badge.kind)}`}>
          {badge.label}
        </span>
      ))}
    </div>
  );
}

function MarketChartAiSummary({ model }: { model: MarketChartModel }) {
  const symbol = model.symbolState;
  const priceDigits = marketPriceDigits(model.latestPrice);
  const plan = asRecord(symbol.pending_entry_plan);
  const volumeProfile = planVolumeProfileDetails(symbol) ?? featureVolumeProfile(model);
  const decision = symbol.risk_guard.decision ?? symbol.ai_decision.decision;
  const planSide = asNonEmptyString(plan?.side);
  const entryMin = asFiniteNumber(plan?.entry_zone_min);
  const entryMax = asFiniteNumber(plan?.entry_zone_max);
  const stopLoss = asFiniteNumber(plan?.stop_loss) ?? asFiniteNumber(plan?.invalidation_price);
  const takeProfit = asFiniteNumber(plan?.take_profit);
  const riskPctCap = asFiniteNumber(plan?.risk_pct_cap);
  const leverageCap = asFiniteNumber(plan?.leverage_cap);
  const planStatus = asNonEmptyString(plan?.plan_status);
  const riskOutcome = summarizeRiskGate(symbol);
  const rows = [
    ["진입 구간", priceRangeText(entryMin, entryMax, priceDigits)],
    ["목표가", formatNumber(takeProfit, priceDigits)],
    ["손절/무효화", formatNumber(stopLoss, priceDigits)],
    [
      "리스크",
      riskPctCap !== null || leverageCap !== null
        ? `${formatUnsignedPercent(riskPctCap, 2)} / ${formatNumber(leverageCap, 1)}x`
      : riskOutcome.label,
    ],
    ...volumeProfileSummaryRows(volumeProfile, priceDigits),
  ];

  return (
    <aside className="rounded-md border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">AI 판단</p>
          <h4 className="mt-1 text-sm font-semibold text-slate-950">
            {translateDecision(planSide ?? decision)}
          </h4>
        </div>
        <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${badgeClass(riskOutcome.kind)}`}>
          {formatRatio(symbol.ai_decision.confidence)}
        </span>
      </div>
      <p className="mt-2 text-xs leading-5 text-slate-600">
        {planStatus ? `${formatInternalCodeLabel(planStatus)} / ${formatDateTime(symbol.pending_entry_plan?.created_at)}` : "조건부 진입 계획 없음"}
      </p>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {rows.map(([label, value]) => (
          <div key={label} className="rounded-md bg-slate-50 px-3 py-2">
            <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">{label}</p>
            <p className="mt-1 break-words text-xs font-semibold text-slate-900">{value}</p>
          </div>
        ))}
      </div>
    </aside>
  );
}

function MarketEntryFlowSummary({ model }: { model: MarketChartModel }) {
  const symbol = model.symbolState;
  const review = aiReviewSummary(symbol);
  const riskOutcome = summarizeRiskGate(symbol);
  const currentCycle = summarizeCurrentCycleSelection(symbol);
  const riskReasons =
    symbol.risk_guard.blocked_reason_codes.length > 0 ? symbol.risk_guard.blocked_reason_codes : symbol.blocked_reasons;
  const effectiveDecision = symbol.risk_guard.decision ?? symbol.ai_decision.decision;
  const isEntryReady = symbol.risk_guard.allowed === true && isEntryDecision(effectiveDecision);
  const rows = [
    {
      label: "시장 조건",
      value: String(symbol.market_context_summary.primary_regime ?? "-"),
      detail: `추세 ${String(symbol.market_context_summary.trend_alignment ?? "-")} / 거래량 ${String(
        symbol.market_context_summary.volume_regime ?? "-",
      )}`,
      kind: symbol.feature_input_delayed || symbol.stale_flags.length > 0 ? ("warn" as const) : ("neutral" as const),
    },
    {
      label: "AI 판단",
      value: `${translateDecision(symbol.ai_decision.decision)} / ${review.label}`,
      detail: `${review.detail} / ${currentCycle.label}`,
      kind: symbol.ai_decision.decision === "hold" ? ("neutral" as const) : ("good" as const),
    },
    {
      label: "리스크 상태",
      value: riskOutcome.label,
      detail:
        riskReasons.length > 0
          ? formatTranslatedCodeList(riskReasons)
          : riskOutcome.detail,
      kind: riskOutcome.kind,
    },
  ];

  return (
    <aside className="rounded-md border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-sm font-semibold text-slate-950">
          {isEntryReady ? "신규진입 상태 요약" : "왜 신규진입 아님"}
        </h4>
        <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${badgeClass(isEntryReady ? "good" : riskOutcome.kind)}`}>
          {isEntryReady ? "가능" : "대기/차단"}
        </span>
      </div>
      <div className="mt-3 space-y-3">
        {rows.map((row) => (
          <div key={row.label} className="rounded-md bg-slate-50 px-3 py-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">{row.label}</p>
              <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${badgeClass(row.kind)}`}>
                {row.kind === "danger" ? "차단" : row.kind === "warn" ? "주의" : row.kind === "good" ? "통과" : "확인"}
              </span>
            </div>
            <p className="mt-2 text-sm font-semibold text-slate-950">{row.value}</p>
            <p className="mt-1 text-xs leading-5 text-slate-600">{row.detail}</p>
          </div>
        ))}
      </div>
    </aside>
  );
}

function MarketComparisonGrid({
  models,
  selectedCandleWindow,
  selectedTimeframe,
}: {
  models: MarketChartModel[];
  selectedCandleWindow: CandleWindow;
  selectedTimeframe: MarketChartTimeframe;
}) {
  const rankedModels = [...models].sort((left, right) => (right.stats.changePct ?? -Infinity) - (left.stats.changePct ?? -Infinity));
  return (
    <div className="mt-5 grid gap-4 xl:grid-cols-2">
      {rankedModels.map((model, index) => (
        <article key={model.symbol} className="rounded-lg border border-slate-200 bg-slate-50 p-4 [contain-intrinsic-size:320px] [content-visibility:auto]">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-slate-900 px-2 py-1 text-xs font-semibold text-white">#{index + 1}</span>
                <h3 className="text-base font-semibold text-slate-950">{model.symbol}</h3>
              </div>
              <p className="mt-1 text-xs text-slate-500">{model.timeframe} / {model.sourceNote}</p>
            </div>
            <Link
              href={marketChartHref({
                selectedSymbol: model.symbol,
                candleWindow: selectedCandleWindow,
                timeframe: selectedTimeframe,
              })}
              className="rounded-md border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700 hover:border-blue-200 hover:text-blue-700"
            >
              열기
            </Link>
          </div>
          <MarketCandlestickSvg model={model} compact />
          <div className="mt-3 grid gap-2 text-xs text-slate-600 sm:grid-cols-3">
            <p>변화 {formatPercentPoint(model.stats.changePct, 2)}</p>
            <p>현재 위치 {formatNumber(model.rangeStats.rangePositionPct, 0)}%</p>
            <p>거래량 {model.stats.volumeVsAverage === null ? "-" : `${formatNumber(model.stats.volumeVsAverage, 2)}배`}</p>
          </div>
        </article>
      ))}
    </div>
  );
}

function latestCandleTimestampForModels(models: MarketChartModel[]) {
  let latestTimestamp: string | null = null;
  let latestTimeMs = 0;
  for (const model of models) {
    const candle = model.candles[model.candles.length - 1];
    const candleTimeMs = timestampMs(candle?.timestamp);
    if (candle?.timestamp && candleTimeMs !== null && candleTimeMs >= latestTimeMs) {
      latestTimestamp = candle.timestamp;
      latestTimeMs = candleTimeMs;
    }
  }
  return latestTimestamp;
}

function MarketChartSection({
  models,
  selectedSymbol,
  selectedCandleWindow,
  selectedTimeframe,
  timeframeAvailability,
}: {
  models: MarketChartModel[];
  selectedSymbol: string;
  selectedCandleWindow: CandleWindow;
  selectedTimeframe: MarketChartTimeframe;
  timeframeAvailability: Map<MarketChartTimeframe, { enabled: boolean; detail: string }>;
}) {
  const latestCandleTime = latestCandleTimestampForModels(models);
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <MarketChartAutoRefresh latestCandleTime={latestCandleTime} timeframe={selectedTimeframe} />
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">시장 차트</p>
          <h2 className="mt-2 text-xl font-semibold text-slate-950">{marketTimeframeLabel(selectedTimeframe)} 가격 흐름과 보조 지표</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            최신 시장 스냅샷의 캔들과 지표를 함께 봅니다. 현재 1시간/4시간은 15분 OHLC를 집계하고, 보조 지표는 다중 타임프레임 값을 사용합니다.
          </p>
        </div>
        <div className="flex flex-col items-start gap-3 lg:items-end">
          <span className="w-fit rounded-md bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
            {models.length > 0 ? `${models.length}개 심볼` : "차트 데이터 없음"}
          </span>
          <div className="flex rounded-md border border-slate-200 bg-slate-50 p-1" aria-label="차트 timeframe">
            {marketChartTimeframeOptions.map((option) => {
              const active = selectedTimeframe === option.value;
              const availability = timeframeAvailability.get(option.value);
              if (!availability?.enabled) {
                return (
                  <span
                    key={option.value}
                    title={availability?.detail ?? "현재 데이터 없음"}
                    className="rounded px-3 py-1 text-xs font-semibold text-slate-300"
                    aria-disabled
                  >
                    {option.label}
                  </span>
                );
              }
              return (
                <Link
                  key={option.value}
                  href={marketChartHref({
                    selectedSymbol,
                    candleWindow: selectedCandleWindow,
                    timeframe: option.value,
                  })}
                  title={availability.detail}
                  className={`rounded px-3 py-1 text-xs font-semibold transition ${
                    active ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-white hover:text-slate-950"
                  }`}
                >
                  {option.label}
                </Link>
              );
            })}
          </div>
          <div className="flex rounded-md border border-slate-200 bg-slate-50 p-1" aria-label="차트 표시 봉 수">
            {marketCandleWindowOptions.map((option) => {
              const active = selectedCandleWindow === option.value;
              return (
                <Link
                  key={option.value}
                  href={marketChartHref({
                    selectedSymbol,
                    candleWindow: option.value,
                    timeframe: selectedTimeframe,
                  })}
                  className={`rounded px-3 py-1 text-xs font-semibold transition ${
                    active ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-white hover:text-slate-950"
                  }`}
                >
                  {marketCandleWindowLabel(option.value, selectedTimeframe)}
                </Link>
              );
            })}
          </div>
        </div>
      </div>

      {models.length === 0 ? (
        <div className="mt-5 rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-sm text-slate-500">
          선택한 심볼 기준으로 시장 스냅샷이 없습니다.
        </div>
      ) : selectedSymbol === "ALL" ? (
        <MarketComparisonGrid
          models={models}
          selectedCandleWindow={selectedCandleWindow}
          selectedTimeframe={selectedTimeframe}
        />
      ) : (
        <div className="mt-5 grid gap-4">
          {models.map((model) => (
            <article key={model.symbol} className="rounded-lg border border-slate-200 bg-slate-50 p-4 [contain-intrinsic-size:720px] [content-visibility:auto]">
              <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h3 className="text-base font-semibold text-slate-950">{model.symbol}</h3>
                  <p className="mt-1 text-xs text-slate-500">
                    {model.timeframe} / {model.sourceNote} / 스냅샷 {formatDateTime(model.snapshotTime)}
                  </p>
                  <MarketChartStatusBadges model={model} />
                </div>
                <div className="text-left sm:text-right">
                  <p className="text-xs font-medium text-slate-500">현재가</p>
                  <p className="mt-1 text-lg font-semibold text-slate-950">
                    {formatNumber(model.latestPrice, marketPriceDigits(model.latestPrice))}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">현재 봉 {formatDateTime(model.candles[model.candles.length - 1]?.timestamp)}</p>
                </div>
              </div>
              <MarketChartStatsStrip model={model} />
              <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
                <MarketCandlestickSvg model={model} />
                <div className="space-y-4">
                  <MarketChartAiSummary model={model} />
                  <MarketEntryFlowSummary model={model} />
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function translateDecision(value: string | null | undefined) {
  if (value === "long") {
    return "롱";
  }
  if (value === "short") {
    return "숏";
  }
  if (value === "reduce") {
    return "축소";
  }
  if (value === "exit") {
    return "청산";
  }
  if (value === "hold") {
    return "신규 진입 대기";
  }
  return value ?? "-";
}

function isEntryDecision(value: string | null | undefined) {
  return value === "long" || value === "short";
}

function translateAiSkipReason(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    NO_EVENT: "검토 이벤트 없음",
    TRIGGER_DEDUPED: "동일 지문 중복",
    AI_DISABLED: "AI 비활성화",
    AI_FAILURE_BACKOFF: "AI 실패 백오프",
    AI_COOLDOWN_ACTIVE: "AI 쿨다운 유지",
    PROTECTION_REVIEW_DETERMINISTIC_ONLY: "보호 검토는 결정론 경로만 사용",
    ENTRY_CANDIDATE_WEAK_VOLUME_PREAI: "거래량 부족으로 AI 검토 생략",
    MACRO_EVENT_IMMINENT: "주요 경제 이벤트 임박으로 신규 진입 보수화",
    MACRO_EVENT_RISK_WINDOW_ACTIVE: "거시 이벤트 리스크 구간",
    STALE_MARKET_DATA: "시장 데이터 지연으로 AI 검토 생략",
    LOW_SCORE: "점수 부족으로 AI 검토 생략",
    SPREAD_STRESS: "스프레드 부담으로 AI 검토 생략",
    EXPOSURE_LIMIT: "노출 한도로 AI 검토 생략",
  };
  return labels[value] ?? value;
}

function translateAiReviewType(value: string | null | undefined, fallbackTriggerReason?: string | null) {
  const reviewType = asNonEmptyString(value);
  if (reviewType) {
    return aiReviewTypeLabelMap[reviewType] ?? reviewType;
  }
  if (fallbackTriggerReason) {
    return triggerReasonReviewLabelMap[fallbackTriggerReason] ?? describeAiTriggerReason(fallbackTriggerReason).label;
  }
  return "-";
}

function getAiSkipReason(symbol: OperatorSymbol) {
  const ai = aiDecisionReadModel(symbol);
  const review = aiReviewReadModel(symbol);
  return (
    asNonEmptyString(review?.skip_reason) ??
    asNonEmptyString(ai.ai_skip_reason) ??
    asNonEmptyString(ai.last_ai_skip_reason)
  );
}

function getAiTriggerReasonCodes(symbol: OperatorSymbol) {
  const ai = aiDecisionReadModel(symbol);
  const reviewCodes = asStringArray(aiReviewReadModel(symbol)?.trigger_reason_codes);
  return reviewCodes.length > 0 ? reviewCodes : asStringArray(ai.ai_trigger_reason_codes);
}

function getAiTriggerReason(symbol: OperatorSymbol) {
  return asNonEmptyString(aiReviewReadModel(symbol)?.trigger_reason) ?? symbol.ai_decision.last_ai_trigger_reason;
}

function aiReviewTypeLabel(symbol: OperatorSymbol) {
  const ai = aiDecisionReadModel(symbol);
  const review = aiReviewReadModel(symbol);
  return translateAiReviewType(review?.review_type ?? ai.ai_review_type, getAiTriggerReason(symbol));
}

function aiReviewReasonHint(symbol: OperatorSymbol) {
  const reasonCodes = getAiTriggerReasonCodes(symbol);
  if (reasonCodes.length > 0) {
    return `사유 ${formatTranslatedCodeList(reasonCodes)}`;
  }
  const triggerReason = getAiTriggerReason(symbol);
  const trigger = describeAiTriggerReason(triggerReason);
  if (trigger.legacy) {
    return trigger.hint;
  }
  return triggerReason
    ? `호출 사유 ${trigger.label}`
    : "호출 사유 없음";
}

function aiMarketSignalSummary(symbol: OperatorSymbol) {
  const ai = aiDecisionReadModel(symbol);
  const marketSignalContext = asRecord(ai.market_signal_context);
  return (
    asNonEmptyString(ai.market_signal_summary) ??
    asNonEmptyString(marketSignalContext?.summary) ??
    asNonEmptyString(ai.ai_trigger_summary) ??
    "지표 근거 없음"
  );
}

function aiMacroEventContext(symbol: OperatorSymbol) {
  const ai = aiDecisionReadModel(symbol);
  return (
    asMacroEventContextSummary(ai.macro_event_risk_summary) ??
    asMacroEventContextSummary(ai.macro_event_context_summary)
  );
}

function aiReviewSummary(symbol: OperatorSymbol) {
  const ai = aiDecisionReadModel(symbol);
  const review = aiReviewReadModel(symbol);
  const trigger = describeAiTriggerReason(getAiTriggerReason(symbol));
  const skipReason = getAiSkipReason(symbol);
  const providerStatus = asNonEmptyString(review?.provider_status);
  const triggerDeduped = review?.trigger_deduped === true || symbol.ai_decision.trigger_deduped === true;
  if (skipReason === "NO_EVENT") {
    return { label: "이번 주기 AI 미호출", detail: "검토 이벤트 없음" };
  }
  if (triggerDeduped || skipReason === "TRIGGER_DEDUPED" || providerStatus === "deduped") {
    return { label: "AI 재검토 생략", detail: review?.dedupe_reason ?? "직전 검토와 변화 없음" };
  }
  if (skipReason || providerStatus === "skipped_pre_ai" || review?.provider_skipped === true) {
    return {
      label: "이번 주기 AI 검토 생략",
      detail: translateAiSkipReason(skipReason ?? providerStatus),
    };
  }
  if (
    review?.provider_invoked === true ||
    providerStatus === "invoked" ||
    review?.invoked_at ||
    ai.last_ai_invoked_at ||
    ai.provider_name
  ) {
    return {
      label: trigger.legacy ? "과거 정책 기록" : "AI 검토 실행",
      detail: aiReviewTypeLabel(symbol),
    };
  }
  return { label: "AI 상태 미확정", detail: "-" };
}

function timelineStageBadgeClass(kind: TimelineStage["kind"]) {
  return badgeClass(kind === "danger" ? "danger" : kind === "warn" ? "warn" : kind === "good" ? "good" : "neutral");
}

function aiProviderInvoked(symbol: OperatorSymbol) {
  const ai = aiDecisionReadModel(symbol);
  const review = aiReviewReadModel(symbol);
  return Boolean(
    review?.provider_invoked === true ||
      review?.provider_status === "invoked" ||
      review?.invoked_at ||
      ai.last_ai_invoked_at ||
      ai.provider_name,
  );
}

function buildAiDecisionFlowStages(symbol: OperatorSymbol): TimelineStage[] {
  const ai = aiDecisionReadModel(symbol);
  const review = aiReviewReadModel(symbol);
  const triggerReason = getAiTriggerReason(symbol);
  const triggerPresentation = describeAiTriggerReason(triggerReason);
  const skipReason = getAiSkipReason(symbol);
  const triggerDeduped = review?.trigger_deduped === true || symbol.ai_decision.trigger_deduped === true;
  const riskReasons = symbol.risk_guard.blocked_reason_codes;
  const passiveRiskOnly = hasOnlyPassiveEntryReasons(riskReasons);
  const currentExecutionExists = symbol.execution.order_id !== null && !isHistoricalExecutionRecord(symbol);
  const pendingPlan = pendingEntryPlanPresentation(symbol);
  const effectiveDecision = symbol.risk_guard.decision ?? symbol.ai_decision.decision;

  const eventStage: TimelineStage = triggerReason
    ? {
        key: "event",
        title: "이벤트",
        label: "생성됨",
        detail: triggerPresentation.legacy
          ? `${triggerPresentation.label} / 과거 정책 기록`
          : `호출 사유 ${triggerPresentation.label}`,
        kind: triggerPresentation.legacy ? "warn" : "good",
      }
    : skipReason === "NO_EVENT"
      ? {
          key: "event",
          title: "이벤트",
          label: "없음",
          detail: "이번 주기에 AI 검토 이벤트가 없습니다.",
          kind: "neutral",
        }
      : ai.decision_run_id || ai.created_at
        ? {
            key: "event",
            title: "이벤트",
            label: "기록 있음",
            detail: "decision row는 있지만 이벤트 원인은 확인 불가입니다.",
            kind: "warn",
          }
        : {
            key: "event",
            title: "이벤트",
            label: "확인 불가",
            detail: "저장된 이벤트 또는 decision row가 없습니다.",
            kind: "neutral",
          };

  const aiStage: TimelineStage = aiProviderInvoked(symbol)
    ? {
        key: "ai",
        title: "AI",
        label: "호출됨",
        detail: aiReviewTypeLabel(symbol),
        kind: "good",
      }
    : triggerDeduped || skipReason
      ? {
          key: "ai",
          title: "AI",
          label: skipReason === "NO_EVENT" ? "대기" : "스킵됨",
          detail:
            triggerDeduped || skipReason === "TRIGGER_DEDUPED"
              ? "동일 지문 중복으로 재검토 생략"
              : translateAiSkipReason(skipReason),
          kind: skipReason === "NO_EVENT" ? "neutral" : "warn",
        }
      : triggerReason
        ? {
            key: "ai",
            title: "AI",
            label: "대기",
            detail: "호출 또는 스킵 결과가 아직 기록되지 않았습니다.",
            kind: "warn",
          }
        : {
            key: "ai",
            title: "AI",
            label: "확인 불가",
            detail: "AI 호출/스킵 기록이 없습니다.",
            kind: "neutral",
          };

  const riskStage: TimelineStage =
    symbol.risk_guard.allowed === true
      ? {
          key: "risk",
          title: "Risk",
          label: "승인",
          detail: isEntryDecision(effectiveDecision)
            ? "deterministic risk_guard 신규 진입 승인"
            : "deterministic risk_guard 통과",
          kind: "good",
        }
      : symbol.risk_guard.allowed === false
        ? passiveRiskOnly
          ? {
              key: "risk",
              title: "Risk",
              label: "대기",
              detail: riskReasons.length > 0 ? formatTranslatedCodeList(riskReasons) : "신규 진입 신호가 없습니다.",
              kind: "neutral",
            }
          : {
            key: "risk",
            title: "Risk",
            label: "차단",
            detail: riskReasons.length > 0 ? formatTranslatedCodeList(riskReasons) : "차단 사유 기록 없음",
            kind: "danger",
          }
        : {
            key: "risk",
            title: "Risk",
            label: "미평가",
            detail: "risk_guard 평가 기록이 없습니다.",
            kind: "neutral",
          };

  const executionStage: TimelineStage = currentExecutionExists
    ? {
        key: "execution",
        title: "실행",
        label: symbol.execution.execution_status === "filled" ? "주문 실행" : "주문 제출",
        detail: symbol.execution.execution_status ?? symbol.execution.order_status ?? "execution 상태 확인 필요",
        kind: symbol.execution.execution_status === "filled" ? "good" : "warn",
      }
    : pendingPlan.planActive
      ? {
          key: "execution",
          title: "실행",
          label: "조건부 대기",
          detail: pendingPlan.detail,
          kind: "warn",
        }
      : symbol.risk_guard.allowed === false
        ? passiveRiskOnly
          ? {
              key: "execution",
              title: "실행",
              label: "실행 없음",
              detail: "진입 신호나 트리거 조건이 아직 충족되지 않았습니다.",
              kind: "neutral",
            }
          : {
            key: "execution",
            title: "실행",
            label: "실행 차단",
            detail: "risk_guard 차단으로 신규 주문이 실행되지 않습니다.",
            kind: "danger",
          }
        : symbol.risk_guard.allowed === true && isEntryDecision(effectiveDecision)
          ? {
              key: "execution",
              title: "실행",
              label: "실행 없음",
              detail: "리스크 승인은 주문 체결 기록이 아니며, 주문 또는 pending plan 기록이 없습니다.",
              kind: "warn",
            }
          : {
              key: "execution",
              title: "실행",
              label: "실행 없음",
              detail: "현재 주문 실행 대상 기록이 없습니다.",
              kind: "neutral",
            };

  return [eventStage, aiStage, riskStage, executionStage];
}

function AiDecisionFlowTimeline({ symbol }: { symbol: OperatorSymbol }) {
  const stages = buildAiDecisionFlowStages(symbol);
  return (
    <div className="mt-5 rounded-md border border-slate-200 bg-slate-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-slate-950">AI 판단 흐름</p>
          <p className="mt-1 text-xs leading-5 text-slate-600">
            이벤트 생성, AI 호출/스킵, deterministic risk_guard, execution/pending 상태를 분리해서 봅니다.
          </p>
        </div>
        <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-600">
          {symbol.symbol}
        </span>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-4">
        {stages.map((stage) => (
          <div key={stage.key} className="rounded-md border border-slate-200 bg-white p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">{stage.title}</p>
              <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${timelineStageBadgeClass(stage.kind)}`}>
                {stage.label}
              </span>
            </div>
            <p className="mt-3 break-words text-xs leading-5 text-slate-600">{stage.detail}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function classifyRiskReasonGroup(value: string): RiskReasonGroupKey {
  const normalized = value.trim();
  const upper = normalized.toUpperCase();
  const lower = normalized.toLowerCase();

  if (
    upper.includes("STALE") ||
    upper.includes("OUTDATED") ||
    upper.includes("INCOMPLETE") ||
    upper.includes("UNTRUSTED") ||
    lower.includes("신선도") ||
    lower.includes("지연") ||
    lower.includes("오래") ||
    lower.includes("불완전")
  ) {
    return "freshness";
  }

  if (
    upper.includes("PROTECTION") ||
    upper.includes("PROTECTIVE") ||
    upper.includes("STOP") ||
    upper.includes("TAKE_PROFIT") ||
    lower.includes("protective order") ||
    lower.includes("missing protection") ||
    lower.includes("take profit") ||
    lower.includes("보호") ||
    lower.includes("손절") ||
    lower.includes("익절")
  ) {
    return "protection";
  }

  if (
    upper.includes("APPROVAL") ||
    upper.includes("LIVE_ARM") ||
    upper.includes("LIVE_DISARM") ||
    upper.includes("LIVE_TRADING_DISABLED") ||
    upper.includes("LIVE_TRADING_ENV_DISABLED") ||
    lower.includes("approval window") ||
    lower.includes("live approval") ||
    lower.includes("승인") ||
    lower.includes("실거래")
  ) {
    return "approval";
  }

  if (
    upper.includes("ENTRY_TRIGGER_NOT_MET") ||
    upper.includes("TRIGGER_NOT_MET") ||
    lower.includes("trigger not met") ||
    lower.includes("진입 조건") ||
    lower.includes("트리거")
  ) {
    return "trigger";
  }

  if (
    upper.includes("EXPOSURE") ||
    upper.includes("DIRECTIONAL") ||
    upper.includes("SINGLE_POSITION") ||
    upper.includes("SAME_TIER") ||
    upper.includes("CORRELATION") ||
    upper.includes("CONCENTRATION") ||
    upper.includes("NOTIONAL") ||
    lower.includes("노출") ||
    lower.includes("편중") ||
    lower.includes("집중") ||
    lower.includes("상관") ||
    lower.includes("최대 단일") ||
    lower.includes("총 노출") ||
    lower.includes("동일 tier") ||
    lower.includes("동일 티어")
  ) {
    return "exposure";
  }

  return "other";
}

function groupedRiskReasons(values: string[] | null | undefined) {
  const groups = new Map<RiskReasonGroupKey, string[]>();
  const seen = new Set<string>();
  for (const value of values ?? []) {
    const reason = typeof value === "string" ? value.trim() : "";
    if (!reason || seen.has(reason)) {
      continue;
    }
    seen.add(reason);
    const group = classifyRiskReasonGroup(reason);
    groups.set(group, [...(groups.get(group) ?? []), reason]);
  }
  return riskReasonGroupOrder
    .map((key) => ({ key, reasons: groups.get(key) ?? [] }))
    .filter((item) => item.reasons.length > 0);
}

function RiskReasonGroups({
  reasons,
  emptyText = "차단 사유 없음",
}: {
  reasons: string[] | null | undefined;
  emptyText?: string;
}) {
  const groups = groupedRiskReasons(reasons);
  if (groups.length === 0) {
    return (
      <div className="rounded-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500">
        {emptyText}
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {groups.map((group) => (
        <div key={group.key} className="rounded-md border border-slate-200 bg-white px-4 py-3">
          <p className="text-sm font-semibold text-slate-900">{riskReasonGroupLabels[group.key]}</p>
          <p className="mt-1 text-xs leading-5 text-slate-500">{riskReasonGroupHints[group.key]}</p>
          <div className="mt-3 space-y-2">
            {group.reasons.map((reason) => (
              <div key={reason} className="rounded-md bg-slate-50 px-3 py-2">
                <p className="text-sm font-medium text-slate-800">{formatInternalCodeLabel(reason)}</p>
                <p className="mt-1 break-all font-mono text-[11px] text-slate-500">{reason}</p>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function pendingEntryPlanPresentation(symbol: OperatorSymbol) {
  const plan = symbol.pending_entry_plan;
  if (!plan?.plan_id) {
    return {
      label: "조건부 진입 대기 없음",
      detail: "현재 저장된 pending entry plan이 없습니다.",
      kind: "neutral" as const,
      planActive: false,
    };
  }

  const mode = formatInternalCodeLabel(plan.entry_mode);
  if (plan.plan_status === "armed") {
    return {
      label: "조건부 진입 대기",
      detail: `AI 판단은 생성되었지만 즉시 주문이 아닙니다. ${mode} 조건 충족 후 risk/execution 재검증이 필요합니다.`,
      kind: "warn" as const,
      planActive: true,
    };
  }
  if (plan.plan_status === "triggered") {
    return {
      label: "조건 확인 후 실행 경로",
      detail: "pending plan 조건이 충족된 기록입니다. 실제 주문/체결 여부는 execution 상태에서 별도로 확인합니다.",
      kind: "warn" as const,
      planActive: true,
    };
  }
  if (plan.plan_status === "canceled") {
    return {
      label: "조건부 진입 취소",
      detail: plan.canceled_reason ? `취소 사유: ${plan.canceled_reason}` : "pending entry plan이 취소되었습니다.",
      kind: "neutral" as const,
      planActive: false,
    };
  }
  if (plan.plan_status === "expired") {
    return {
      label: "조건부 진입 만료",
      detail: "조건 충족 전 pending entry plan이 만료되었습니다.",
      kind: "neutral" as const,
      planActive: false,
    };
  }
  return {
    label: "조건부 진입 상태 확인",
    detail: "pending entry plan 상태를 확인할 수 없습니다.",
    kind: "neutral" as const,
    planActive: true,
  };
}

function protectionReviewPresentation(symbol: OperatorSymbol) {
  const protection = protectionReadModel(symbol);
  const reason = protection.blocked_reason_code ?? protection.blocked_reason ?? protection.last_error;
  const deterministicHint = "AI 판단과 별개로 결정론적 보호 주문 점검/복구 경로에서 확인합니다.";

  if (!symbol.open_position.is_open && protection.status === "flat") {
    return {
      label: "보호 주문 대상 없음",
      detail: `오픈 포지션이 없어 보호 주문 점검 대상이 없습니다. ${deterministicHint}`,
      kind: "neutral" as const,
    };
  }
  if (protection.auto_recovery_active) {
    return {
      label: "보호 주문 복구 경로 진행",
      detail: `${deterministicHint} 복구 상태 ${protection.recovery_status ?? "진행 중"}.`,
      kind: "warn" as const,
    };
  }
  if (
    protection.verification_status === "verify_failed" ||
    protection.blocked_reason_code ||
    protection.protected === false ||
    protection.missing_components.length > 0
  ) {
    return {
      label: "보호 주문 점검 필요",
      detail: `${reason ? `${reason}. ` : ""}보호 주문이 확인되지 않으면 신규 진입은 차단될 수 있습니다.`,
      kind: "danger" as const,
    };
  }
  if (protection.recovery_status) {
    return {
      label: "protection recovery 상태",
      detail: `${deterministicHint} 현재 상태 ${protection.recovery_status}.`,
      kind: "neutral" as const,
    };
  }
  if (protection.protected) {
    return {
      label: "보호 주문 확인됨",
      detail: `${deterministicHint} 손절 ${protection.has_stop_loss ? "확인" : "없음"} / 익절 ${
        protection.has_take_profit ? "확인" : "없음"
      }.`,
      kind: "good" as const,
    };
  }
  return {
    label: "보호 주문 확인 불가",
    detail: deterministicHint,
    kind: "neutral" as const,
  };
}

function translateOperatingState(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return operatingStateLabelMap[value] ?? value;
}

function translateSchedulerStatus(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return schedulerStatusLabelMap[value] ?? value;
}

function translateReasonCode(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const extraReasonCodeLabelMap: Record<string, string> = {
    ENTRY_AUTO_RESIZED: "진입 수량이 자동 축소 승인되었습니다.",
    ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT: "총 노출 한도에 맞게 진입 수량이 축소되었습니다.",
    ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT: "방향 편향 한도에 맞게 진입 수량이 축소되었습니다.",
    ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT: "최대 단일 포지션 한도에 맞게 진입 수량이 축소되었습니다.",
    ENTRY_CLAMPED_TO_SAME_TIER_LIMIT: "동일 티어 집중도 한도에 맞게 진입 수량이 축소되었습니다.",
    ENTRY_SIZE_BELOW_MIN_NOTIONAL: "최소 실행 가능 주문 미만",
    ENTRY_TRIGGER_NOT_MET: "진입 트리거 미충족",
    CHASE_LIMIT_EXCEEDED: "추격 진입 한도 초과",
    INVALID_INVALIDATION_PRICE: "무효화 가격 기준 이상",
    ENGINE_TREND_CONTINUATION_ENGINE: "추세 지속 엔진 기준",
    REGIME_BEARISH: "하락 레짐",
    TREND_BEARISH_ALIGNED: "하락 추세 정렬",
    EXPECTANCY_NEUTRAL: "기대값 중립",
    DERIVATIVES_NEUTRAL: "파생시장 중립",
    LEAD_MARKETS_ALIGNED: "선행 시장 정렬",
    SETUP_TIME_PROFILE_CONTINUATION_BALANCED: "지속형 진입 시간대 균형",
    PROVIDER_OPENAI: "OpenAI 검토",
    HOLDING_PROFILE_SWING_ALLOWED: "스윙 보유 조건 허용",
    HOLDING_PROFILE_INTRADAY_ALIGNMENT: "단기 보유 조건 정렬",
    DETERMINISTIC_HARD_STOP_ACTIVE: "고정 손절 기준 활성",
    LONG_HOLDING_PROFILE_QUALITY_INSUFFICIENT: "롱 보유 품질 근거 부족",
  };
  return extraReasonCodeLabelMap[value] ?? reasonCodeLabelMap[value] ?? value;
}

function formatTranslatedCodeList(values: string[] | null | undefined) {
  if (!values || values.length === 0) {
    return "-";
  }
  return values.map(formatInternalCodeLabel).join(", ");
}

function internalCodeHint(values: string[] | null | undefined) {
  if (!values || values.length === 0) {
    return "내부 코드 없음";
  }
  return "내부 코드는 원본 payload에서 확인";
}

function formatInternalCodeLabel(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    capacity_reached: "새 주문 슬롯 미배정",
    ranked_portfolio_focus: "포트폴리오 우선순위 선정",
    breadth_hold_bias: "시장 폭 약화로 대기",
    score_below_threshold: "진입 점수 부족",
    low_edge_hold_candidate: "우위가 약해 대기",
    low_conviction_slot_excluded: "확신도 낮음으로 미선정",
    underperforming_expectancy_bucket: "최근 기대값 버킷 약화",
    expectancy_below_threshold: "기대값 기준 부족",
    adverse_signed_slippage: "체결 품질 불리",
    duplicate_exposure: "중복 노출 방지",
    swing: "스윙 보유",
    intraday: "단기 보유",
    scalp: "초단기 보유",
    none: "해당 없음",
    pullback_confirm: "눌림 확인",
    breakout_confirm: "돌파 확인",
    immediate: "즉시 진입",
  };
  if (labels[value]) {
    return labels[value];
  }
  const translated = translateReasonCode(value);
  if (translated !== value) {
    return translated;
  }
  const displayValue = formatDisplayValue(value);
  if (displayValue !== value) {
    return displayValue;
  }
  return "내부 조건 확인";
}

function formatCapacityReason(value: string | null | undefined) {
  if (!value || value === "-") {
    return "-";
  }
  const labels: Record<string, string> = {
    capacity_reached: "새 주문 슬롯 미배정",
    ranked_portfolio_focus: "포트폴리오 우선순위 선정",
    trend_expansion_allow_rotation: "추세 확장 구간, 회전 허용",
    transition_fragile_reduce_capacity: "전환 구간, 수용 한도 축소",
    low_edge_hold_candidate: "우위가 약해 대기",
    breadth_hold_bias: "시장 폭 약화로 대기",
  };
  return labels[value] ?? formatInternalCodeLabel(value);
}

function rawCodeDetails(title: string, values: string[] | null | undefined) {
  if (!values || values.length === 0) {
    return null;
  }
  return (
    <details className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
      <summary className="cursor-pointer list-none text-sm font-semibold text-slate-800">{title}</summary>
      <div className="mt-3 flex flex-wrap gap-2">
        {values.map((code) => (
          <span key={code} className="rounded-md bg-white px-2.5 py-1 text-xs font-medium text-slate-600">
            {code}
          </span>
        ))}
      </div>
    </details>
  );
}

function formatAiExplanation(value: string | null | undefined) {
  if (!value) {
    return "최신 판단 설명이 없습니다.";
  }
  const trimmed = value.trim();
  const known: Record<string, string> = {
    "Long-horizon entry was bounded by data quality.":
      "장기 진입은 데이터 품질 제한 때문에 보수적으로 대기 중입니다.",
  };
  if (known[trimmed]) {
    return known[trimmed];
  }
  if (/^[\x00-\x7F]+$/.test(trimmed)) {
    return "AI 판단 설명 원문은 고급 정보에서 확인하세요.";
  }
  return trimmed;
}

function positionStatusText(symbol: OperatorDashboardPayload["symbols"][number]) {
  if (!symbol.open_position.is_open) {
    return {
      label: "열린 포지션 없음",
      detail: "현재 보유 중인 포지션이 없습니다.",
    };
  }
  return {
    label: `${symbol.open_position.side ?? "-"} / ${symbol.open_position.quantity ?? 0}`,
    detail: `진입가 ${symbol.open_position.entry_price ?? "-"} / 현재가 ${symbol.open_position.mark_price ?? "-"}`,
  };
}

function portfolioLimitText(applied: boolean) {
  return applied ? "포트폴리오 한도 적용" : "포트폴리오 한도 미적용";
}

function shortFingerprint(value: string | null | undefined) {
  if (!value || value === "-") {
    return "없음";
  }
  if (value.length <= 14) {
    return value;
  }
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

function hardStopLabel(symbol: OperatorDashboardPayload["symbols"][number]) {
  if (symbol.open_position.hard_stop_active === true) {
    return "활성";
  }
  if (symbol.open_position.hard_stop_active === false) {
    return "비활성";
  }
  return symbol.open_position.is_open ? "미확인" : "-";
}

function stopWideningLabel(symbol: OperatorDashboardPayload["symbols"][number]) {
  if (symbol.open_position.stop_widening_allowed === false) {
    return "금지";
  }
  if (symbol.open_position.stop_widening_allowed === true) {
    return "허용";
  }
  return symbol.open_position.is_open ? "미확인" : "-";
}

function asRiskCheckRow(row: Row): RiskCheckRow {
  return {
    id: typeof row.id === "number" ? row.id : null,
    symbol: typeof row.symbol === "string" ? row.symbol : null,
    decision_run_id: typeof row.decision_run_id === "number" ? row.decision_run_id : null,
    allowed: typeof row.allowed === "boolean" ? row.allowed : null,
    decision: typeof row.decision === "string" ? row.decision : null,
    reason_codes: Array.isArray(row.reason_codes)
      ? row.reason_codes.filter((item): item is string => typeof item === "string")
      : [],
    approved_risk_pct: typeof row.approved_risk_pct === "number" ? row.approved_risk_pct : null,
    approved_leverage: typeof row.approved_leverage === "number" ? row.approved_leverage : null,
    ai_review: asAiReviewReadModel(row.ai_review),
    ai_review_type: typeof row.ai_review_type === "string" ? row.ai_review_type : null,
    ai_trigger_reason: typeof row.ai_trigger_reason === "string" ? row.ai_trigger_reason : null,
    ai_trigger_reason_codes: asStringArray(row.ai_trigger_reason_codes),
    ai_skip_reason: typeof row.ai_skip_reason === "string" ? row.ai_skip_reason : null,
    last_ai_skip_reason: typeof row.last_ai_skip_reason === "string" ? row.last_ai_skip_reason : null,
    ai_trigger_summary: typeof row.ai_trigger_summary === "string" ? row.ai_trigger_summary : null,
    market_signal_summary: typeof row.market_signal_summary === "string" ? row.market_signal_summary : null,
    macro_event_context_summary: asMacroEventContextSummary(row.macro_event_context_summary),
    macro_event_risk_summary: asMacroEventContextSummary(row.macro_event_risk_summary),
    created_at: typeof row.created_at === "string" ? row.created_at : null,
    payload:
      row.payload && typeof row.payload === "object" && !Array.isArray(row.payload)
        ? (row.payload as Record<string, unknown>)
        : null,
  };
}

function riskAllowedPresentation(value: boolean | null | undefined, reasonCodes: string[] | null | undefined) {
  if (value === true) {
    return { label: "리스크 통과", hint: "신규 진입 허용", kind: "good" as const };
  }
  if (value === false) {
    if (hasOnlyPassiveEntryReasons(reasonCodes)) {
      return { label: "신규 진입 대기", hint: "진입 신호나 트리거 조건이 아직 충족되지 않았습니다.", kind: "neutral" as const };
    }
    return { label: "리스크 차단", hint: "신규 진입 차단", kind: "danger" as const };
  }
  return { label: "리스크 미확정", hint: "허용 여부 미확정", kind: "neutral" as const };
}

function riskTriggerReasonPresentation(row: RiskCheckRow) {
  const reviewType = asNonEmptyString(row.ai_review?.review_type) ?? row.ai_review_type;
  const triggerReason = asNonEmptyString(row.ai_review?.trigger_reason) ?? row.ai_trigger_reason;
  if (reviewType) {
    return translateAiReviewType(reviewType, triggerReason);
  }
  if (triggerReason) {
    return describeAiTriggerReason(triggerReason).label;
  }
  if (row.decision_run_id === null) {
    return "linked decision 없음";
  }
  return "legacy row";
}

function riskTriggerReasonHint(row: RiskCheckRow, fallback: string) {
  const reasonCodes = asStringArray(row.ai_review?.trigger_reason_codes);
  const effectiveReasonCodes = reasonCodes.length > 0 ? reasonCodes : row.ai_trigger_reason_codes ?? [];
  const triggerReason = asNonEmptyString(row.ai_review?.trigger_reason) ?? row.ai_trigger_reason;
  if (effectiveReasonCodes.length > 0) {
    return `사유 ${formatTranslatedCodeList(effectiveReasonCodes)}`;
  }
  if (triggerReason) {
    const trigger = describeAiTriggerReason(triggerReason);
    return trigger.legacy ? trigger.hint : `trigger ${triggerReason}`;
  }
  return fallback;
}

function riskTriggerSummaryPresentation(row: RiskCheckRow) {
  if (row.market_signal_summary && row.market_signal_summary.trim().length > 0) {
    return row.market_signal_summary;
  }
  if (row.ai_trigger_summary && row.ai_trigger_summary.trim().length > 0) {
    return row.ai_trigger_summary;
  }
  if (row.decision_run_id === null) {
    return "linked decision 없음";
  }
  if (row.ai_trigger_reason && row.ai_trigger_reason.trim().length > 0) {
    return "지표 근거 없음";
  }
  return "legacy row";
}

function riskSkipReason(row: RiskCheckRow) {
  return (
    asNonEmptyString(row.ai_review?.skip_reason) ??
    asNonEmptyString(row.ai_skip_reason) ??
    asNonEmptyString(row.last_ai_skip_reason)
  );
}

function riskMacroEventContext(row: RiskCheckRow) {
  return row.macro_event_risk_summary ?? row.macro_event_context_summary ?? null;
}

function SymbolTabs({
  slug,
  symbols,
  selectedSymbol,
  includeAll = false,
  extraQuery,
}: {
  slug: string;
  symbols: string[];
  selectedSymbol: string;
  includeAll?: boolean;
  extraQuery?: Record<string, string>;
}) {
  const items = includeAll ? ["ALL", ...symbols] : symbols;
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((symbol) => {
        const active = selectedSymbol === symbol;
        const params = new URLSearchParams(extraQuery);
        if (symbol !== "ALL") {
          params.set("symbol", symbol);
        } else {
          params.delete("symbol");
        }
        const query = params.toString();
        const href = query ? `/dashboard/${slug}?${query}` : `/dashboard/${slug}`;
        return (
          <Link
            key={symbol}
            href={href}
            className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${
              active
                ? "border-blue-600 bg-blue-600 text-white"
                : "border-slate-200 bg-white text-slate-700 hover:border-blue-200 hover:bg-blue-50"
            }`}
          >
            {symbol === "ALL" ? "전체" : symbol}
          </Link>
        );
      })}
    </div>
  );
}

export function MarketSignalView({
  operator,
  snapshots,
  features,
  selectedSymbol,
  selectedCandleWindow,
  selectedTimeframe,
  selectedChartZoomRange,
  chartCandlesBySymbol = {},
}: {
  operator: OperatorDashboardPayload;
  snapshots: Row[];
  features: Row[];
  selectedSymbol: string;
  selectedCandleWindow: CandleWindow;
  selectedTimeframe: MarketChartTimeframe;
  selectedChartZoomRange: MarketChartZoomRange | null;
  chartCandlesBySymbol?: MarketChartCandlesBySymbol;
}) {
  const trackedSymbols = Array.isArray(operator.control.tracked_symbols)
    ? operator.control.tracked_symbols
    : operator.symbols.map((item) => item.symbol);
  const symbols =
    selectedSymbol === "ALL"
      ? operator.symbols
      : operator.symbols.filter((item) => item.symbol === selectedSymbol);

  const filteredSnapshots =
    selectedSymbol === "ALL"
      ? snapshots
      : snapshots.filter((row) => String(row.symbol ?? "").toUpperCase() === selectedSymbol);
  const filteredFeatures =
    selectedSymbol === "ALL"
      ? features
      : features.filter((row) => String(row.symbol ?? "").toUpperCase() === selectedSymbol);
  const timeframeAvailability = availableMarketTimeframes(filteredSnapshots, filteredFeatures);
  const effectiveTimeframe = timeframeAvailability.get(selectedTimeframe)?.enabled ? selectedTimeframe : "15m";
  const chartModels = buildMarketChartModels(
    symbols,
    filteredSnapshots,
    filteredFeatures,
    selectedCandleWindow,
    effectiveTimeframe,
    chartCandlesBySymbol,
    selectedChartZoomRange,
  );
  const formatMarketSnapshotRowTitle = (row: Row, index: number) => {
    const symbol = typeof row.symbol === "string" ? row.symbol : null;
    const timeframe = typeof row.timeframe === "string" ? row.timeframe : null;
    if (symbol && timeframe) {
      return `${symbol} / 시장 ${timeframe}`;
    }
    if (symbol) {
      return symbol;
    }
    return `항목 ${index + 1}`;
  };

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">시장 / 신호</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">시장 입력과 신호 입력만 분리해서 확인</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          이 탭은 가격과 지표 입력을 보여줍니다. AI 판단, 리스크 승인, 실제 실행 상태는 다른 탭에서 별도로 확인합니다.
        </p>
        <div className="mt-4">
          <SymbolTabs
            slug="market"
            symbols={trackedSymbols}
            selectedSymbol={selectedSymbol}
            includeAll
            extraQuery={{
              candles: String(selectedCandleWindow),
              timeframe: effectiveTimeframe,
            }}
          />
        </div>
      </section>

      <MarketChartSection
        models={chartModels}
        selectedSymbol={selectedSymbol}
        selectedCandleWindow={selectedCandleWindow}
        selectedTimeframe={effectiveTimeframe}
        timeframeAvailability={timeframeAvailability}
      />

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex flex-col gap-2 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">시장 입력 요약</p>
            <h2 className="mt-2 text-xl font-semibold text-slate-950">심볼별 최신 입력 상태</h2>
          </div>
          <p className="text-sm text-slate-500">차트와 같은 backend snapshot 계열을 기준으로 표시합니다.</p>
        </div>
        <div className="mt-5 grid gap-4 xl:grid-cols-2">
          {symbols.map((symbol) => {
            const featureInputMissing = symbol.stale_flags.includes("feature_input_missing");
            const featureInputDelayed = symbol.feature_input_delayed;
            return (
            <div key={symbol.symbol} className="rounded-md border border-slate-200 bg-white p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-lg font-semibold text-slate-950">{symbol.symbol}</h3>
                  <p className="mt-1 text-sm text-slate-500">
                    {formatMarketTiming(symbol)}
                  </p>
                </div>
                <span
                  className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                    featureInputDelayed ? "danger" : symbol.stale_flags.length > 0 ? "warn" : "good",
                  )}`}
                >
                  {featureInputDelayed ? "입력 지연" : symbol.stale_flags.length > 0 ? "입력 주의" : "입력 정상"}
                </span>
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                {metricCard("현재가", formatNumber(symbol.latest_price), "선택 심볼 기준 최신 가격")}
                {metricCard(
                  "시장 레짐",
                  String(symbol.market_context_summary.primary_regime ?? "-"),
                  featureInputDelayed
                    ? "피처 입력 생성 지연"
                    : featureInputMissing
                      ? "피처 입력 대기"
                    : `정렬 ${String(symbol.market_context_summary.trend_alignment ?? "-")}`,
                )}
                {metricCard(
                  "변동성 / 거래량",
                  `${String(symbol.market_context_summary.volatility_regime ?? "-")} / ${String(
                    symbol.market_context_summary.volume_regime ?? "-",
                  )}`,
                  featureInputDelayed ? "지표 입력 생성 지연" : featureInputMissing ? "지표 입력 대기" : "시장 상태 입력",
                )}
                {metricCard(
                  "신선도",
                  featureInputDelayed ? "지연" : symbol.stale_flags.length > 0 ? "주의" : "정상",
                  featureInputDelayed
                    ? `지표 입력 없음, ${symbol.feature_input_delay_minutes ?? "-"}분 경과`
                    : symbol.stale_flags.length > 0
                    ? symbol.stale_flags.map(translateMarketInputFlag).join(", ")
                    : "이상 플래그 없음",
                )}
              </div>
              {featureInputMissing ? (
                <div
                  className={`mt-3 rounded-2xl px-4 py-3 text-sm leading-6 ${
                    featureInputDelayed
                      ? "border border-rose-200 bg-rose-50 text-rose-900"
                      : "border border-amber-200 bg-amber-50 text-amber-900"
                  }`}
                >
                  {featureInputDelayed
                    ? `시장 스냅샷은 수집됐지만 지표 입력 생성이 ${symbol.feature_input_delay_minutes ?? "-"}분째 지연되고 있습니다. 시장 ${symbol.timeframe ?? "-"} 기준 예상 대기 ${symbol.feature_input_delay_threshold_minutes ?? "-"}분을 넘겼습니다.`
                    : "시장 스냅샷은 수집됐지만 피처 입력은 아직 생성되지 않았습니다."}
                </div>
              ) : null}
            </div>
          )})}
        </div>
      </section>

      <DataTable
        title="시장 스냅샷"
        description="최근 가격 입력"
        rows={filteredSnapshots}
        emptyStateTitle="표시할 시장 스냅샷이 없습니다."
        emptyStateDescription="선택한 심볼 기준으로 아직 저장된 시장 스냅샷이 없습니다."
        hiddenColumns={["candle_count", "candles", "payload"]}
        rowTitleFormatter={formatMarketSnapshotRowTitle}
        labelOverrides={{ timeframe: "시장 타임프레임" }}
      />

      <DataTable
        title="특성 입력"
        description="최근 지표 계산 결과"
        rows={filteredFeatures}
        emptyStateTitle="표시할 지표 입력이 없습니다."
        emptyStateDescription="선택한 심볼 기준으로 아직 계산된 지표 스냅샷이 없습니다."
      />
    </div>
  );
}

export function DecisionView({
  operator,
  decisionRows,
  selectedSymbol,
}: {
  operator: OperatorDashboardPayload;
  decisionRows: Row[];
  selectedSymbol: string;
}) {
  const symbol =
    operator.symbols.find((item) => item.symbol === selectedSymbol) ?? operator.symbols[0] ?? null;
  const filteredDecisionRows = decisionRows.filter(
    (row) => String(row.symbol ?? "").toUpperCase() === (symbol?.symbol ?? ""),
  );
  const recommendation = symbol ? summarizeLastAiRecommendation(symbol) : null;
  const riskOutcome = symbol ? summarizeRiskGate(symbol) : null;
  const review = symbol ? aiReviewSummary(symbol) : null;
  const currentCycle = symbol ? summarizeCurrentCycleSelection(symbol) : null;
  const execution = symbol ? summarizeExecutionState(symbol) : null;
  const recentExecution = symbol ? summarizeRecentExecutionRecord(symbol) : null;
  const positionStatus = symbol ? positionStatusText(symbol) : null;
  const historicalGapNotice = symbol ? describeHistoricalDecisionGap(symbol) : null;
  const triggerPresentation = symbol
    ? describeAiTriggerReason(getAiTriggerReason(symbol))
    : null;
  const aiReviewLabel = symbol ? aiReviewTypeLabel(symbol) : "-";
  const aiReviewHint = symbol ? aiReviewReasonHint(symbol) : "-";
  const aiSkipReason = symbol ? getAiSkipReason(symbol) : null;
  const marketSignalSummary = symbol ? aiMarketSignalSummary(symbol) : "지표 근거 없음";
  const macroEventContext = symbol ? aiMacroEventContext(symbol) : null;
  const macroEventSummary = formatMacroEventContextSummary(macroEventContext);
  const macroEventDetail = formatMacroEventContextDetail(macroEventContext);
  const effectiveRiskReasons = symbol
    ? symbol.risk_guard.blocked_reason_codes.length > 0
      ? symbol.risk_guard.blocked_reason_codes
      : symbol.blocked_reasons
    : [];
  const blockedReasonText = symbol
    ? formatTranslatedCodeList(effectiveRiskReasons)
    : "-";
  const aiSlotValue = symbol ? symbol.ai_decision.assigned_slot ?? "-" : "-";
  const aiCandidateWeightValue = symbol ? symbol.ai_decision.candidate_weight : null;
  const aiCapacityReasonValue = symbol ? symbol.ai_decision.capacity_reason ?? "-" : "-";
  const aiCapacityReasonLabel = symbol ? formatCapacityReason(symbol.ai_decision.capacity_reason) : "-";
  const riskSlotValue = symbol ? symbol.risk_guard.assigned_slot ?? "-" : "-";
  const riskCandidateWeightValue = symbol ? symbol.risk_guard.candidate_weight : null;
  const riskCapacityReasonValue = symbol ? symbol.risk_guard.capacity_reason ?? "-" : "-";
  const riskCapacityReasonLabel = symbol ? formatCapacityReason(symbol.risk_guard.capacity_reason) : "-";
  const currentSlotValue = symbol ? symbol.candidate_selection.assigned_slot ?? "-" : "-";
  const currentCandidateWeightValue = symbol ? symbol.candidate_selection.candidate_weight : null;
  const currentCapacityReasonValue = symbol ? symbol.candidate_selection.capacity_reason ?? "-" : "-";
  const currentCapacityReasonLabel = symbol ? formatCapacityReason(symbol.candidate_selection.capacity_reason) : "-";
  const currentHoldingProfileValue = symbol ? symbol.candidate_selection.holding_profile ?? "-" : "-";
  const currentHoldingProfileReasonValue = symbol
    ? formatInternalCodeLabel(symbol.candidate_selection.holding_profile_reason)
    : "-";
  const executionTimestamp = symbol
    ? symbol.execution.created_at ??
      symbol.execution.execution_created_at ??
      symbol.pending_entry_plan?.created_at ??
      null
    : null;
  const pendingPlan = symbol ? pendingEntryPlanPresentation(symbol) : null;
  const protectionReview = symbol ? protectionReviewPresentation(symbol) : null;
  const decisionTableEmptyDescription = historicalGapNotice
        ? "저장된 판단 기록은 없지만 상단 카드에는 마지막 AI 스냅샷이 남아 있을 수 있습니다. 이번 판단 주기 상태와는 구분해서 보세요."
    : "선택한 심볼 기준으로 아직 저장된 decision row가 없습니다.";

  if (symbol === null) {
    return (
      <DataTable
        title="의사결정"
        description="평가 / 판단"
        rows={[]}
        emptyStateTitle="표시할 의사결정이 없습니다."
        emptyStateDescription="추적 심볼이 없거나 아직 평가 데이터가 없습니다."
      />
    );
  }

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">의사결정</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">{symbol.symbol} 현재 판단</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          현재 결론, 이번 판단 주기 주문 여부, 보유 포지션을 먼저 보여줍니다.
        </p>
        <div className="mt-4 grid gap-3 lg:grid-cols-3">
          {metricCard("현재 결론", recommendation?.detail ?? "-", currentCycle?.detail ?? "-")}
          {metricCard("이번 판단 주기 주문", execution?.label ?? "-", execution?.detail ?? "-")}
          {metricCard("포지션 상태", positionStatus?.label ?? "-", positionStatus?.detail ?? "-")}
        </div>
        <div className="mt-4">
          <SymbolTabs
            slug="decisions"
            symbols={operator.control.tracked_symbols}
            selectedSymbol={symbol.symbol}
          />
        </div>
        <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-5 text-slate-600">
          {getSelectedSymbolPolicyHint("single")}
        </div>
        <div className="mt-5 grid gap-4 lg:grid-cols-4">
          {metricCard("마지막 AI 스냅샷", formatDateTime(symbol.ai_decision.created_at), "상단 AI 카드는 과거 스냅샷일 수 있습니다.")}
          {metricCard("이번 주기 AI 상태", review?.label ?? "-", review?.detail ?? "-")}
          {metricCard(
            "최근 AI 호출 시각",
            formatDateTime(symbol.ai_decision.last_ai_invoked_at),
            triggerPresentation?.legacy
              ? `사유 ${triggerPresentation.label} / 현재 런타임 트리거가 아니라 저장된 과거 정책 기록입니다.`
              : `검토 분류 ${aiReviewLabel}`,
          )}
          {metricCard(
            "이번 판단 주기 기준",
            formatDateTime(operator.generated_at),
            "현재 대시보드 새로고침 기준으로 후보 선정과 실행 상태를 보여줍니다.",
          )}
        </div>
        <AiDecisionFlowTimeline symbol={symbol} />
        <div className="mt-4 grid gap-4 xl:grid-cols-4">
          <div className="rounded-md border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                  recommendation?.kind ?? "neutral",
                )}`}
              >
                {recommendation?.label ?? "마지막 AI 추천"}
              </span>
              <span className="text-xs text-slate-500">{formatDateTime(symbol.ai_decision.created_at)}</span>
            </div>
            <p className="mt-4 text-2xl font-semibold text-slate-950">{translateDecision(symbol.ai_decision.decision)}</p>
            <p className="mt-2 text-sm text-slate-600">신뢰도 {formatRatio(symbol.ai_decision.confidence)}</p>
            <p className="mt-3 text-sm leading-6 text-slate-700">
              {formatAiExplanation(symbol.ai_decision.explanation_short)}
            </p>
            {historicalGapNotice ? (
              <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900">
                {historicalGapNotice}
              </div>
            ) : null}
            {triggerPresentation?.legacy ? (
              <div className="mt-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-700">
                {triggerPresentation.hint}
              </div>
            ) : null}
            <div className="mt-4 flex flex-wrap gap-2">
              {symbol.ai_decision.rationale_codes.length > 0 ? (
                symbol.ai_decision.rationale_codes.map((code) => (
                  <span key={code} className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700">
                    {formatInternalCodeLabel(code)}
                  </span>
                ))
              ) : (
                <span className="text-sm text-slate-500">근거 코드 없음</span>
              )}
            </div>
            {rawCodeDetails("내부 근거 코드 보기", symbol.ai_decision.rationale_codes)}
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {metricCard(
                triggerPresentation?.legacy ? "과거 정책 기록" : "AI 호출 분류",
                aiReviewLabel,
                triggerPresentation?.legacy ? triggerPresentation.hint : aiReviewHint,
              )}
              {aiSkipReason
                ? metricCard("AI 검토 생략", translateAiSkipReason(aiSkipReason), "판단 전 생략 여부")
                : null}
              {metricCard(
                "당시 시장 신호 요약",
                marketSignalSummary,
                "AI 호출 원인이 아니라 판단 당시 지표 요약입니다.",
              )}
              {metricCard("이벤트 리스크", macroEventSummary, macroEventDetail)}
              {metricCard("AI 최종 판단", translateDecision(symbol.ai_decision.decision), "마지막 AI 응답 기준")}
              {metricCard(
                "최근 AI 호출 시각",
                formatDateTime(symbol.ai_decision.last_ai_invoked_at),
                "세부 지문은 고급 정보에서 확인",
              )}
              {metricCard("AI 기준 슬롯", aiSlotValue, `가중치 ${aiCandidateWeightValue ?? "-"}`)}
              {metricCard(
                "AI 기준 수용 한도",
                aiCapacityReasonLabel,
                portfolioLimitText(symbol.ai_decision.portfolio_slot_soft_cap_applied),
              )}
            </div>
            {rawCodeDetails("AI 내부 기준 보기", [
              aiCapacityReasonValue,
              symbol.ai_decision.trigger_fingerprint ?? "-",
            ].filter((value) => value !== "-"))}
          </div>

          <div className="rounded-md border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                  riskOutcome?.kind ?? "neutral",
                )}`}
              >
                {riskOutcome?.label ?? "리스크 판정 없음"}
              </span>
              <span className="text-xs text-slate-500">{formatDateTime(symbol.risk_guard.created_at)}</span>
            </div>
            <div className="mt-4 space-y-3">
              {metricCard(
                riskOutcome?.label === "신규 진입 대기" ? "신규 진입 대기 사유" : "리스크 차단 사유",
                blockedReasonText,
                "현재 리스크 가드 기준",
              )}
              <div>
                <p className="mb-2 text-xs font-medium text-slate-500">리스크 사유 그룹</p>
                <RiskReasonGroups reasons={effectiveRiskReasons} />
              </div>
              {metricCard(
                "리스크 기준 슬롯",
                riskSlotValue,
                `가중치 ${riskCandidateWeightValue ?? "-"} / ${riskCapacityReasonLabel}`,
              )}
              {metricCard(
                "리스크 승인 프로필",
                symbol.risk_guard.approved_leverage !== null ? `${symbol.risk_guard.approved_leverage}x` : "-",
                `허용 리스크 ${formatRatio(symbol.risk_guard.approved_risk_pct)}`,
              )}
              {metricCard(
                "리스크 포트폴리오 한도",
                symbol.risk_guard.portfolio_slot_soft_cap_applied ? "적용" : "미적용",
                `수용 한도 ${riskCapacityReasonLabel}`,
              )}
            </div>
            {rawCodeDetails("리스크 내부 코드 보기", [
              ...symbol.risk_guard.blocked_reason_codes,
              riskCapacityReasonValue,
            ].filter((value) => value !== "-"))}
            {symbol.risk_guard.allowed === true && symbol.execution.order_id === null ? (
              <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900">
                리스크 통과는 주문 제출 완료가 아닙니다. 이번 판단 주기 선정 여부와 진입 대기 플랜, 실제 주문 상태를 함께 확인하세요.
              </div>
            ) : null}
          </div>

          <div className="rounded-md border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                  currentCycle?.kind ?? "neutral",
                )}`}
              >
                {currentCycle?.label ?? "이번 판단 주기 선택"}
              </span>
              <span className="text-xs text-slate-500">{formatDateTime(operator.generated_at)}</span>
            </div>
            <p className="mt-4 text-sm leading-6 text-slate-700">{currentCycle?.detail ?? "-"}</p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {metricCard("이번 판단 주기 슬롯", currentSlotValue, `가중치 ${currentCandidateWeightValue ?? "-"}`)}
              {metricCard(
                "이번 판단 주기 수용 한도",
                currentCapacityReasonLabel,
                portfolioLimitText(symbol.candidate_selection.portfolio_slot_soft_cap_applied),
              )}
              {metricCard("홀딩 프로필", formatInternalCodeLabel(currentHoldingProfileValue), currentHoldingProfileReasonValue)}
              {metricCard(
                "현재 시장 요약",
                String(symbol.market_context_summary.primary_regime ?? "-"),
                `정렬 ${String(symbol.market_context_summary.trend_alignment ?? "-")}`,
              )}
              {metricCard(
                "이번 판단 주기 차단 사유",
                formatTranslatedCodeList(symbol.candidate_selection.blocked_reason_codes),
                internalCodeHint(symbol.candidate_selection.blocked_reason_codes),
              )}
              {metricCard(
                "이번 판단 주기 사유",
                formatInternalCodeLabel(
                  symbol.candidate_selection.selected_reason ??
                    symbol.candidate_selection.rejected_reason ??
                    symbol.candidate_selection.selection_reason,
                ),
                "이번 판단 주기 기준 선택/미선정 사유",
                { compact: true },
              )}
            </div>
            {rawCodeDetails("이번 판단 주기 내부 코드 보기", [
              ...symbol.candidate_selection.blocked_reason_codes,
              currentCapacityReasonValue,
              currentHoldingProfileValue,
              symbol.candidate_selection.selected_reason ??
                symbol.candidate_selection.rejected_reason ??
                symbol.candidate_selection.selection_reason ??
                "-",
            ].filter((value) => value !== "-"))}
          </div>

          <div className="rounded-md border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                  execution?.kind ?? "neutral",
                )}`}
              >
                {execution?.label ?? "실제 실행"}
              </span>
              <span className="text-xs text-slate-500">{formatDateTime(executionTimestamp)}</span>
            </div>
            <div className="mt-4 space-y-3">
              {metricCard("이번 판단 주기 주문", execution?.label ?? "-", execution?.detail ?? "-")}
              {recentExecution
                ? metricCard("최근 과거 실행 기록", recentExecution.label, recentExecution.detail)
                : null}
              {metricCard("진입 대기 플랜", pendingPlan?.label ?? "-", pendingPlan?.detail ?? "-", { compact: true })}
              {pendingPlan?.planActive ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900">
                  조건부 진입 대기입니다. AI 판단 완료와 실제 주문 실행은 별도 상태이며, 설정된 조건 충족 후 risk/execution 재검증을 거쳐야 합니다.
                </div>
              ) : null}
              {metricCard("보호 주문 점검", protectionReview?.label ?? "-", protectionReview?.detail ?? "-", {
                compact: true,
              })}
              {metricCard("하드 스탑", hardStopLabel(symbol), `손절 확대 ${stopWideningLabel(symbol)}`)}
              {metricCard(
                "오픈 포지션",
                positionStatus?.label ?? "-",
                positionStatus?.detail ?? "-",
              )}
            </div>
          </div>
        </div>
      </section>

      <DataTable
        title="최근 평가 기록"
        description="저장된 decision row"
        rows={filteredDecisionRows}
        emptyStateTitle="최근 평가 기록이 없습니다."
        emptyStateDescription={decisionTableEmptyDescription}
      />
    </div>
  );
}

export function SchedulerView({
  operator,
  schedulerRows,
}: {
  operator: OperatorDashboardPayload;
  schedulerRows: Row[];
}) {
  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">스케줄러</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">주기와 검토 예정 상태 확인</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          이 탭은 AI 판단 자체보다 언제 검토가 예정되어 있는지, 왜 건너뛰었는지 또는 중복 처리되었는지, 마지막 호출과 다음 예정 시각이
          언제인지에 집중합니다.
        </p>
        <div className="mt-5 grid gap-4 lg:grid-cols-4">
          {metricCard("현재 상태", translateSchedulerStatus(operator.control.scheduler_status), "최근 스케줄러 실행 상태")}
          {metricCard("실행 윈도우", operator.control.scheduler_window ?? "-", "현재 대표 실행 주기")}
          {metricCard("다음 실행 예정", formatDateTime(operator.control.scheduler_next_run_at), "전역 스케줄 기준")}
          {metricCard(
            "운영 상태",
            operator.control.trading_paused ? "시스템 가드 모드" : translateOperatingState(operator.control.operating_state),
            "상세 차단 사유는 의사결정 탭에서 확인",
          )}
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">AI 검토 상태</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">심볼별 AI 검토 / 생략 상태</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          실제 검토 분류, AI 호출 전 생략 사유, 당시 시장 신호 요약, 이벤트 리스크, 포트폴리오 한도와 차단 사유를 심볼별로 바로 읽을 수 있습니다.
          보호 상태 점검은 AI가 보호 주문을 직접 생성/복구한다는 뜻이 아니라, 별도 결정론적 protection recovery 상태와 분리해서 봅니다.
        </p>
        <div className="mt-5 grid gap-4 xl:grid-cols-3">
          {operator.symbols.map((symbol) => {
            const aiReview = aiReviewReadModel(symbol);
            const review = aiReviewSummary(symbol);
            const triggerPresentation = describeAiTriggerReason(getAiTriggerReason(symbol));
            const reviewLabel = aiReviewTypeLabel(symbol);
            const reviewHint = aiReviewReasonHint(symbol);
            const skipReason = getAiSkipReason(symbol);
            const marketSummary = aiMarketSignalSummary(symbol);
            const macroEventContext = aiMacroEventContext(symbol);
            const macroEventSummary = formatMacroEventContextSummary(macroEventContext);
            const macroEventDetail = formatMacroEventContextDetail(macroEventContext);
            return (
              <div key={symbol.symbol} className="rounded-md border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-base font-semibold text-slate-950">{symbol.symbol}</h3>
                    <p className="mt-1 text-xs text-slate-500">{symbol.timeframe ?? "-"}</p>
                  </div>
                  <span
                    className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                      aiReview?.trigger_deduped === true || symbol.ai_decision.trigger_deduped
                        ? "warn"
                        : aiReview?.provider_invoked === true ||
                            aiReview?.provider_status === "invoked" ||
                            symbol.ai_decision.last_ai_invoked_at
                          ? "good"
                          : skipReason
                            ? "neutral"
                            : "neutral",
                    )}`}
                  >
                    {review.label}
                  </span>
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  {metricCard(
                    triggerPresentation.legacy ? "과거 정책 기록" : "AI 호출 분류",
                    reviewLabel,
                    triggerPresentation.legacy ? triggerPresentation.hint : reviewHint,
                    { compact: true },
                  )}
                  {metricCard(
                    "AI 검토 생략",
                    translateAiSkipReason(skipReason),
                    skipReason ? "판단 전 생략 여부" : "생략 사유 없음",
                    { compact: true },
                  )}
                  {metricCard(
                    "당시 시장 신호 요약",
                    marketSummary,
                    "AI 호출 원인이 아니라 판단 당시 지표 요약입니다.",
                    { compact: true },
                  )}
                  {metricCard("이벤트 리스크", macroEventSummary, macroEventDetail, { compact: true })}
                  {metricCard(
                    "최근 AI 호출 시각",
                    formatDateTime(aiReview?.invoked_at ?? symbol.ai_decision.last_ai_invoked_at),
                    `제공자 ${formatDisplayValue(aiReview?.provider_name ?? symbol.ai_decision.provider_name ?? "-", "provider_name")}`,
                    { compact: true },
                  )}
                  {metricCard(
                    "AI 호출 지문",
                    shortFingerprint(aiReview?.trigger_fingerprint ?? symbol.ai_decision.trigger_fingerprint),
                    "이벤트 기반 호출에서 동일 지문 재호출 방지에 사용합니다.",
                    { compact: true },
                  )}
                  {metricCard(
                    "슬롯 / 포트폴리오 한도",
                    symbol.ai_decision.assigned_slot ?? symbol.candidate_selection.assigned_slot ?? "-",
                    portfolioLimitText(symbol.risk_guard.portfolio_slot_soft_cap_applied),
                    { compact: true },
                  )}
                  {metricCard(
                    "차단 사유",
                    formatTranslatedCodeList(
                      symbol.risk_guard.blocked_reason_codes.length > 0
                        ? symbol.risk_guard.blocked_reason_codes
                        : symbol.candidate_selection.blocked_reason_codes,
                    ),
                    internalCodeHint(
                      symbol.risk_guard.blocked_reason_codes.length > 0
                        ? symbol.risk_guard.blocked_reason_codes
                        : symbol.candidate_selection.blocked_reason_codes,
                    ),
                    { compact: true },
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <DataTable
        title="스케줄러 실행 기록"
        description="주기 상태와 결과"
        rows={schedulerRows}
        emptyStateTitle="표시할 스케줄러 기록이 없습니다."
        emptyStateDescription="아직 scheduler run이 저장되지 않았습니다."
      />
    </div>
  );
}

export function RiskView({
  riskRows,
  alertRows,
}: {
  riskRows: Row[];
  alertRows: Row[];
}) {
  const rows = riskRows.map(asRiskCheckRow);

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">리스크 점검</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">AI 검토 분류와 리스크 결과를 한 카드에서 확인</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          실제 AI 검토 분류, 당시 시장 신호 요약, 이벤트 리스크, 허용/차단 결과와 승인 리스크/레버리지를 분리해서 보여줍니다.
          근거가 부족한 예전 row는 추정하지 않고 legacy 여부를 그대로 드러냅니다.
        </p>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">리스크 카드</p>
            <h2 className="mt-2 text-xl font-semibold text-slate-950">최근 리스크 점검</h2>
          </div>
          <div className="w-fit rounded-md bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
            {rows.length}건
          </div>
        </div>

        {rows.length === 0 ? (
          <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-sm text-slate-500">
            <p className="font-semibold text-slate-700">표시할 리스크 점검이 없습니다.</p>
            <p className="mt-2 leading-6">저장된 리스크 판정 기록이 아직 없습니다.</p>
          </div>
        ) : (
          <div className="grid gap-4 xl:grid-cols-2">
            {rows.map((row, index) => {
              const allowed = riskAllowedPresentation(row.allowed, row.reason_codes);
              const triggerReason = riskTriggerReasonPresentation(row);
              const triggerSummary = riskTriggerSummaryPresentation(row);
              const skipReason = riskSkipReason(row);
              const macroEventContext = riskMacroEventContext(row);
              const macroEventSummary = formatMacroEventContextSummary(macroEventContext);
              const macroEventDetail = formatMacroEventContextDetail(macroEventContext);
              const symbolLabel = row.symbol ?? `리스크 ${index + 1}`;
              const decisionRunLabel =
                row.decision_run_id !== null ? `decision #${row.decision_run_id}` : "linked decision 없음";
              const triggerReasonHint = riskTriggerReasonHint(row, decisionRunLabel);

              return (
                <article
                  key={row.id ?? `${row.symbol ?? "risk"}-${row.created_at ?? index}`}
                  className="rounded-lg border border-slate-200 bg-slate-50 p-4"
                >
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <h3 className="text-base font-semibold text-ink">{symbolLabel}</h3>
                      <p className="mt-1 text-xs text-slate-500">
                        {formatDateTime(row.created_at)} / {decisionRunLabel}
                      </p>
                    </div>
                    <span
                      className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(allowed.kind)}`}
                    >
                      {allowed.label}
                    </span>
                  </div>

                  <div className="mt-4 grid gap-3 lg:grid-cols-2">
                    {metricCard("AI 호출 분류", triggerReason, triggerReasonHint, { compact: true })}
                    {skipReason
                      ? metricCard("AI 검토 생략", translateAiSkipReason(skipReason), "판단 전 생략 여부", {
                          compact: true,
                        })
                      : null}
                    {metricCard(
                      "당시 시장 신호 요약",
                      triggerSummary,
                      "AI 호출 원인이 아니라 판단 당시 지표 요약입니다.",
                      { compact: true },
                    )}
                    {metricCard("이벤트 리스크", macroEventSummary, macroEventDetail, { compact: true })}
                    {metricCard("AI 최종 판단", translateDecision(row.decision), "리스크 대상 결정")}
                    {metricCard("허용 여부", allowed.label, allowed.hint)}
                    {metricCard(
                      "차단 사유",
                      formatTranslatedCodeList(row.reason_codes),
                      internalCodeHint(row.reason_codes),
                      { compact: true },
                    )}
                    {metricCard(
                      "승인 리스크 / 레버리지",
                      `${formatRatio(row.approved_risk_pct)} / ${
                        row.approved_leverage !== null ? `${formatNumber(row.approved_leverage, 2)}x` : "-"
                      }`,
                      "허용된 경우에만 의미 있는 승인 수치",
                    )}
                    {metricCard(
                      "판단 기록 ID",
                      row.decision_run_id !== null ? String(row.decision_run_id) : "-",
                      row.decision_run_id !== null ? "연결된 decision row" : "linked decision 없음",
                    )}
                    {metricCard("생성 시각", formatDateTime(row.created_at), "리스크 점검 기록 생성 시각")}
                  </div>

                  <div className="mt-4">
                    <p className="mb-2 text-xs font-medium text-slate-500">리스크 사유 그룹</p>
                    <RiskReasonGroups reasons={row.reason_codes} />
                  </div>

                  {row.payload ? (
                    <details className="mt-4 rounded-md border border-slate-200 bg-white">
                      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-ink">
                        고급 정보 보기
                      </summary>
                      <div className="border-t border-slate-200 px-4 py-4">
                        <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-slate-950 p-4 text-xs leading-6 text-slate-100">
                          {JSON.stringify(row.payload, null, 2)}
                        </pre>
                      </div>
                    </details>
                  ) : null}
                </article>
              );
            })}
          </div>
        )}
      </section>

      <DataTable
        title="운영 알림"
        description="리스크 관련 알림"
        rows={alertRows}
        emptyStateTitle="표시할 알림이 없습니다."
        emptyStateDescription="최근 리스크 관련 알림 기록이 없습니다."
      />
    </div>
  );
}

export function AgentDebugView({ agentRows }: { agentRows: Row[] }) {
  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">에이전트</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">최근 AI 실행 요약</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          실행 상태, 모델, 판단 결과, 핵심 payload만 확인합니다. 세부 판단 흐름은 의사결정 탭을 기준으로 봅니다.
        </p>
      </section>

      <DataTable
        title="에이전트 실행 기록"
        description="AI 실행 요약"
        rows={agentRows}
        emptyStateTitle="표시할 에이전트 실행 기록이 없습니다."
        emptyStateDescription="저장된 agent run이 아직 없습니다."
      />
    </div>
  );
}

