import type { ReactNode } from "react";
import Link from "next/link";

import type { OperatorDashboardPayload } from "./overview-dashboard";
import type { AIUsagePayload } from "./ai-usage-panel";
import { DataTable } from "./data-table";
import type { ClientMarketCandlestickModel } from "./market-candlestick-chart-client";
import type { BinanceChartCandle } from "../lib/binance-chart-candles";
import { RiskCheckPayloadDetails } from "./risk-check-payload-details";
import { getSelectedSymbolPolicyHint } from "../lib/selected-symbol";
import {
  aiSkipReasonTitle,
  describeAiSkipReason,
  describeReasonCode,
  describeReasonCodeInContext,
  isEntryWaitReasonCode,
  isEntryWaitReasonCodeInContext,
  type ReasonCodeCategory,
} from "../lib/risk-reason-copy.js";
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
import {
  summarizeSettlementDisplay,
  type OrderSettlementInput,
} from "../lib/order-settlement";
import { ordersViewHref, type OrderLifecycleTab } from "../lib/orders-query";
import {
  countVisibleMarketChartMarkers,
  dedupeMarketChartEventMarkers,
  type MarketChartEventMarker,
  type MarketChartEventMarkerKind,
} from "../lib/market-chart-markers";
import { buildExecutionRiskProfileSummary } from "../lib/execution-risk-profile-summary";
import {
  formatMarketRawReasonCodes,
  formatMarketReasonCodeDebugLabels,
  formatMarketReasonCodeLabels,
  marketContextValueLabel,
  marketDataSourceLabel,
  marketDataStatusLabel,
  marketDecisionLabel,
  marketReasonCodeLabel,
} from "../lib/market-dashboard-copy";
import {
  buildMarketBlockedReasonDistribution,
  type MarketBlockedReasonDistributionItem,
} from "../lib/market-period-detail-graphs";
import {
  resolveAvailableMarketTimeframes,
  resolveEffectiveMarketTimeframe,
  type MarketTimeframeAvailability,
} from "../lib/market-timeframes";
import { MarketRawDataPanel } from "./market-raw-data-panel";
import { MarketPeriodDetailGraphs } from "./market-period-detail-graphs";

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
  block_scope?: string | null;
  candidate_hold_reason_codes?: string[];
  global_block_reason_codes?: string[];
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
  pending_entry_plan?: Record<string, unknown> | null;
  created_at?: string | null;
  payload?: Record<string, unknown> | null;
};
type PositionRow = {
  id: number | null;
  symbol: string;
  mode: string | null;
  side: string | null;
  status: string | null;
  quantity: number | null;
  entryPrice: number | null;
  markPrice: number | null;
  leverage: number | null;
  stopLoss: number | null;
  takeProfit: number | null;
  realizedPnl: number | null;
  unrealizedPnl: number | null;
  openedAt: string | null;
  updatedAt: string | null;
  protected: boolean | null;
  protectiveOrderCount: number | null;
  hasStopLoss: boolean | null;
  hasTakeProfit: boolean | null;
  missingComponents: string[];
  orderIds: string[];
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
  hold_diagnostic?: Record<string, unknown> | null;
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
type EntryLifecycleTab = "summary" | "plan" | "execution";
type InternalCodeBadge = {
  key: string;
  label: string;
  count: number;
  codes: string[];
};
type OrderHistoryRow = {
  id: number | null;
  symbol: string;
  positionId: number | null;
  parentOrderId: number | null;
  decisionRunId: number | null;
  riskCheckId: number | null;
  side: string | null;
  orderType: string | null;
  status: string | null;
  exchangeStatus: string | null;
  mode: string | null;
  reduceOnly: boolean | null;
  closeOnly: boolean | null;
  requestedQuantity: number | null;
  requestedPrice: number | null;
  filledQuantity: number | null;
  averageFillPrice: number | null;
  externalOrderId: string | null;
  clientOrderId: string | null;
  reasonCodes: string[];
  pnlSource: string | null;
  closeExecutionSyncStatus: string | null;
  realizedPnlConfirmed: boolean | null;
  missingCloseExecution: boolean | null;
  feeSource: string | null;
  feeConfirmed: boolean | null;
  warningMessage: string | null;
  feeWarningMessage: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  lastExchangeUpdateAt: string | null;
};
type ExecutionHistoryRow = {
  id: number | null;
  orderId: number | null;
  positionId: number | null;
  symbol: string;
  status: string | null;
  orderType: string | null;
  orderStatus: string | null;
  fillPrice: number | null;
  fillQuantity: number | null;
  feePaid: number | null;
  realizedPnl: number | null;
  externalTradeId: string | null;
  commissionAsset: string | null;
  reduceOnly: boolean | null;
  closeOnly: boolean | null;
  decisionRunId: number | null;
  createdAt: string | null;
  updatedAt: string | null;
};
type PositionHistoryGroup = {
  key: string;
  positionId: number | null;
  symbol: string;
  orders: OrderHistoryRow[];
  executions: ExecutionHistoryRow[];
  createdAt: string | null;
  updatedAt: string | null;
};
type RiskReasonGroupKey = "freshness" | "exposure" | "approval" | "protection" | "trigger" | "other";
type CandleWindow = 30 | 60 | 120;
export type MarketChartTimeframe = "15m" | "1h" | "4h";
export type MarketChartZoomRange = { start: number; end: number };
type MarketAutoRefreshRenderer = (props: {
  latestCandleTime: string | null;
  timeframe: MarketChartTimeframe;
}) => ReactNode;
type MarketCandlestickRenderer = (model: ClientMarketCandlestickModel) => ReactNode;
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
type MarketChartModel = {
  symbol: string;
  window: CandleWindow;
  timeframe: MarketChartTimeframe;
  sourceTimeframe: string;
  sourceNote: string;
  featureSourceNote: string;
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
  blockedReasonDistribution: MarketBlockedReasonDistributionItem[];
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
  { value: 30, label: "30봉" },
  { value: 60, label: "60봉" },
  { value: 120, label: "120봉" },
];

const marketChartTimeframeOptions: { value: MarketChartTimeframe; label: string }[] = [
  { value: "15m", label: "15분" },
  { value: "1h", label: "1시간" },
  { value: "4h", label: "4시간" },
];

const defaultMarketOverlays: MarketOverlayKey[] = ["close", "volume", "levels", "averageVolume"];
const minMarketChartZoomCandles = 12;
const maxVisibleInternalCodeBadges = 9;
const entryLifecycleTabs: Array<{ key: EntryLifecycleTab; label: string; description: string }> = [
  { key: "summary", label: "흐름 요약", description: "이벤트부터 실행 상태까지 한 줄로 확인" },
  { key: "plan", label: "진입 계획", description: "대기 구간, 감시 상태, 생성 사유 확인" },
  { key: "execution", label: "실행 결과", description: "주문, 체결, 포지션, 보호 주문 확인" },
];

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
  ACCOUNT_STATE_STALE: "계좌 정보 오래됨",
  POSITION_STATE_STALE: "포지션 정보 오래됨",
  OPEN_ORDERS_STATE_STALE: "열린 주문 정보 오래됨",
  PROTECTION_STATE_UNVERIFIED: "보호 주문 검증 불가",
  DETERMINISTIC_BASELINE_DISAGREEMENT: "AI 판단과 기준선 판단이 달라 즉시 주문을 보류했습니다",
  UNRESOLVED_SUBMISSION_GUARD_ACTIVE: "미해결 주문 제출 가드",
  UNRESOLVED_SUBMISSION_DEADLINE_EXCEEDED: "미해결 주문 확인 초과",
  LIVE_ORDER_SUBMISSION_UNKNOWN: "주문 제출 결과 불명확",
  BINANCE_REST_CIRCUIT_OPEN: "Binance REST 회로 열림",
  DRAWDOWN_STATE_CAUTION: "드로다운 주의",
  DRAWDOWN_STATE_CONTAINMENT: "드로다운 억제",
  DRAWDOWN_STATE_RECOVERY: "드로다운 회복",
  ENTRY_CANDIDATE_SELECTED: "신규 진입 후보 선정",
  ENTRY_CANDIDATE_WEAK_VOLUME_PREAI: "거래량 부족으로 AI 검토 생략",
  ENTRY_CANDIDATE_NEUTRAL_CONTEXT_HOLD_BACKOFF: "반복 중립 후보라 AI 검토 생략",
  ENTRY_CANDIDATE_NEUTRAL_CONTEXT_PREAI: "중립 신호라 AI 검토 생략",
  ENTRY_CANDIDATE_LOW_ACTIONABILITY_HOLD_BACKOFF: "반복 저효용 후보라 AI 검토 생략",
  ENTRY_CANDIDATE_ORDER_PATH_NOT_ACTIONABLE: "주문 경로 미준비로 AI 검토 생략",
  ENTRY_CANDIDATE_ACTIVE_PENDING_PLAN_PREAI: "기존 대기 진입안으로 AI 검토 생략",
  ENTRY_CANDIDATE_INCOMPLETE_TRADE_PLAN_PREAI: "진입 구조 불완전으로 AI 검토 생략",
  ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN: "최근 같은 장면의 AI hold 판단 재사용",
  AI_ENTRY_OUTPUT_INCOMPLETE: "AI 진입안 구조 불완전으로 hold 정규화",
  ROLE_DAILY_TOKEN_BUDGET_EXHAUSTED: "일일 AI 토큰 예산 소진",
  SOFT_SIGNAL_AI_REVIEW: "약한 후보 AI 검토 대상",
  SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE: "약한 관망 후보라 AI 호출 전 억제",
  SOFT_SIGNAL_REVIEW_COOLDOWN_ACTIVE: "약한 후보 전환감시 쿨다운",
  SOFT_SIGNAL_REVIEW_NO_MATERIAL_CHANGE: "약한 후보 변화 부족으로 AI 생략",
  AI_CYCLE_BUDGET_EXHAUSTED: "사이클 AI 예산 초과로 신규 후보 검토 생략",
  SOFT_SIGNAL_TRANSITION_WATCH: "약한 후보 전환감시",
  SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_WATCH: "직접 진입 대신 대기 계획",
  SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_HOLD: "약한 후보 직접 진입 차단",
  DERIVATIVES_ALIGNMENT_HEADWIND: "파생시장 정합성 부족",
  BREAKOUT_OI_SPREAD_FILTER: "돌파 OI/스프레드 조건 부족",
  BREAKOUT_OI_NOT_EXPANDING: "돌파 OI 확장 없음",
  MACRO_EVENT_IMMINENT: "주요 경제 이벤트 임박으로 신규 진입 보수화",
  MACRO_EVENT_RISK_WINDOW_ACTIVE: "거시 이벤트 리스크 구간",
  MACRO_RELEASE_REACTION_WINDOW: "발표 직후 변동성 구간",
  MACRO_EVENT_RESULT_AVAILABLE: "경제 이벤트 발표치 반영",
  MACRO_EVENT_RESULT_VS_FORECAST: "발표치가 예상치와 다름",
  MACRO_EVENT_RESULT_VS_PRIOR: "발표치가 이전치와 다름",
  MACRO_EVENT_RESULT_BEARISH: "발표 결과가 위험자산에 부담",
  MACRO_EVENT_RESULT_BULLISH: "발표 결과가 위험자산에 우호적",
  MACRO_EVENT_RESULT_NEUTRAL: "발표 결과 방향성 중립",
  MACRO_EVENT_RESULT_SURPRISE_HIGH: "발표 결과 서프라이즈 큼",
  MACRO_EVENT_RESULT_CONFLICT: "발표 결과와 진입 방향 충돌",
  STALE_MARKET_DATA: "시장 데이터 지연",
  PLAN_CANCELED_NO_ENTRY_CAPACITY: "추가 진입 여유가 없어 플랜 감시 중단",
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
  freshness: "계좌, 포지션, 주문, 시장 데이터가 오래됨/불완전/신뢰 불가 상태인지 확인",
  exposure: "단일 포지션, 방향 편중, 총 노출, 동일 tier 집중도, 상관 위험 한도",
  approval: "실거래 승인 유효시간, 실거래 켜짐/꺼짐, 승인 상태",
  protection: "보호 주문 누락, 미검증, stop/take profit 확인 실패",
  trigger: "대기 진입 계획 또는 진입 트리거 조건 미충족",
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

function asNonEmptyString(value: unknown) {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function asStringArray(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function isPassiveEntryReason(value: string | null | undefined) {
  return isEntryWaitReasonCode(value);
}

function hasOnlyPassiveEntryReasons(values: string[] | null | undefined) {
  const reasons = values?.filter((value) => value.trim().length > 0) ?? [];
  return reasons.length > 0 && reasons.every((reason) => isEntryWaitReasonCodeInContext(reason, reasons));
}

function dedupeNonEmptyReasons(values: string[] | null | undefined) {
  return Array.from(new Set((values ?? []).map((value) => value.trim()).filter((value) => value.length > 0)));
}

function resolveRiskBlockScope(
  scope: string | null | undefined,
  reasonCodes: string[] | null | undefined,
  candidateHoldReasonCodes: string[] | null | undefined,
  globalBlockReasonCodes: string[] | null | undefined,
) {
  const resolvedCandidateHoldReasonCodes = dedupeNonEmptyReasons(candidateHoldReasonCodes);
  const resolvedGlobalBlockReasonCodes = dedupeNonEmptyReasons(globalBlockReasonCodes);
  if (scope === "candidate_hold" || scope === "global_block" || scope === "mixed") {
    return {
      scope,
      candidateHoldReasonCodes: resolvedCandidateHoldReasonCodes,
      globalBlockReasonCodes: resolvedGlobalBlockReasonCodes,
    };
  }
  const resolvedReasonCodes = dedupeNonEmptyReasons(reasonCodes);
  if (resolvedGlobalBlockReasonCodes.length > 0 && resolvedCandidateHoldReasonCodes.length > 0) {
    return {
      scope: "mixed",
      candidateHoldReasonCodes: resolvedCandidateHoldReasonCodes,
      globalBlockReasonCodes: resolvedGlobalBlockReasonCodes,
    };
  }
  if (resolvedGlobalBlockReasonCodes.length > 0) {
    return {
      scope: "global_block",
      candidateHoldReasonCodes: resolvedCandidateHoldReasonCodes,
      globalBlockReasonCodes: resolvedGlobalBlockReasonCodes,
    };
  }
  if (
    resolvedCandidateHoldReasonCodes.length > 0 ||
    (resolvedReasonCodes.length > 0 && hasOnlyPassiveEntryReasons(resolvedReasonCodes))
  ) {
    return {
      scope: "candidate_hold",
      candidateHoldReasonCodes:
        resolvedCandidateHoldReasonCodes.length > 0
          ? resolvedCandidateHoldReasonCodes
          : resolvedReasonCodes.filter((reason) => reason !== "HOLD_DECISION"),
      globalBlockReasonCodes: [],
    };
  }
  return { scope: "none", candidateHoldReasonCodes: [], globalBlockReasonCodes: [] };
}

function riskScopePresentation(
  scope: string | null | undefined,
  candidateHoldReasonCodes: string[] | null | undefined,
  globalBlockReasonCodes: string[] | null | undefined,
) {
  if (scope === "global_block") {
    return {
      label: "전역 거래 차단",
      hint:
        globalBlockReasonCodes && globalBlockReasonCodes.length > 0
          ? formatTranslatedCodeList(globalBlockReasonCodes)
          : "운영 또는 안전 가드가 신규 진입 자체를 막고 있습니다.",
      kind: "danger" as const,
    };
  }
  if (scope === "mixed") {
    return {
      label: "전역 차단 + 후보 관망",
      hint:
        globalBlockReasonCodes && globalBlockReasonCodes.length > 0
          ? `전역 ${formatTranslatedCodeList(globalBlockReasonCodes)} / 후보 ${formatTranslatedCodeList(candidateHoldReasonCodes)}`
          : formatTranslatedCodeList(candidateHoldReasonCodes),
      kind: "danger" as const,
    };
  }
  if (scope === "candidate_hold") {
    return {
      label: "현재 후보 관망",
      hint:
        candidateHoldReasonCodes && candidateHoldReasonCodes.length > 0
          ? formatTranslatedCodeList(candidateHoldReasonCodes)
          : "전역 거래 차단이 아니라 이번 후보 품질 또는 트리거 부족 상태입니다.",
      kind: "neutral" as const,
    };
  }
  return {
    label: "차단 없음",
    hint: "전역 차단 또는 후보 관망 사유가 없습니다.",
    kind: "good" as const,
  };
}

function symbolRiskScope(symbol: OperatorSymbol) {
  return resolveRiskBlockScope(
    symbol.risk_guard.block_scope,
    symbol.risk_guard.blocked_reason_codes,
    symbol.risk_guard.candidate_hold_reason_codes ?? [],
    symbol.risk_guard.global_block_reason_codes ?? [],
  );
}

function symbolRiskReasonCodes(symbol: OperatorSymbol) {
  const resolved = symbolRiskScope(symbol);
  if (resolved.scope === "candidate_hold") {
    return resolved.candidateHoldReasonCodes;
  }
  if (resolved.scope === "global_block") {
    return resolved.globalBlockReasonCodes;
  }
  if (resolved.scope === "mixed") {
    return dedupeNonEmptyReasons([...resolved.globalBlockReasonCodes, ...resolved.candidateHoldReasonCodes]);
  }
  return symbol.risk_guard.blocked_reason_codes.length > 0 ? symbol.risk_guard.blocked_reason_codes : symbol.blocked_reasons;
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
    account: "계좌 정보 오래됨",
    positions: "포지션 정보 오래됨",
    open_orders: "열린 주문 정보 오래됨",
    protective_orders: "보호 주문 정보 오래됨",
    market_snapshot: "시장 스냅샷 오래됨",
    market_snapshot_incomplete: "시장 스냅샷 불완전",
    feature_input_missing: "지표 입력 없음",
  };
  return labels[value] ?? "확인 필요";
}

function formatMarketTiming(symbol: OperatorDashboardPayload["symbols"][number]) {
  const parts: string[] = [];
  if (symbol.market_candle_time) {
    parts.push(`캔들 ${formatDateTime(symbol.market_candle_time)}`);
  }
  if (symbol.market_snapshot_time) {
    parts.push(`수집 ${formatDateTime(symbol.market_snapshot_time)}`);
  }
  return `${parts.join(" / ") || "기록 없음"} / 봉 기준 ${symbol.timeframe ?? "-"}`;
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

function formatCount(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "-";
  }
  return Math.round(value).toLocaleString("ko-KR");
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

function formatUsdValue(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "미집계";
  }
  return `$${value.toLocaleString("ko-KR", {
    minimumFractionDigits: value === 0 ? 2 : 4,
    maximumFractionDigits: 6,
  })}`;
}

function marketFreshnessDisplay(summary: Record<string, unknown> | null) {
  const stream = asRecord(summary?.stream);
  const sourceDetail = asRecord(summary?.source_detail);
  const source = typeof summary?.source === "string" ? summary.source : null;
  const activeSnapshotSource =
    typeof summary?.active_snapshot_source === "string"
      ? summary.active_snapshot_source
      : typeof sourceDetail?.active_snapshot_source === "string"
        ? sourceDetail.active_snapshot_source
        : source;
  const status = typeof summary?.status === "string" ? summary.status : null;
  const sourceStatus = typeof summary?.source_status === "string" ? summary.source_status : null;
  const snapshotAt = typeof summary?.snapshot_at === "string" ? summary.snapshot_at : null;
  const sourceTime = typeof summary?.source_time === "string" ? summary.source_time : typeof sourceDetail?.source_time === "string" ? sourceDetail.source_time : null;
  const receivedAt = typeof summary?.received_at === "string" ? summary.received_at : typeof sourceDetail?.received_at === "string" ? sourceDetail.received_at : null;
  const streamStatus = typeof stream?.status === "string" ? stream.status : null;
  const streamReason = typeof stream?.reason_code === "string" ? stream.reason_code : null;
  const configuredCacheBackend =
    typeof summary?.configured_cache_backend === "string"
      ? summary.configured_cache_backend
      : typeof sourceDetail?.configured_cache_backend === "string"
        ? sourceDetail.configured_cache_backend
        : typeof stream?.configured_cache_backend === "string"
          ? stream.configured_cache_backend
          : typeof summary?.cache_backend === "string"
            ? summary.cache_backend
            : typeof sourceDetail?.cache_backend === "string"
              ? sourceDetail.cache_backend
              : typeof stream?.cache_backend === "string"
                ? stream.cache_backend
                : null;
  const cacheHealth =
    typeof summary?.cache_health === "string"
      ? summary.cache_health
      : typeof sourceDetail?.cache_health === "string"
        ? sourceDetail.cache_health
        : typeof stream?.cache_health === "string"
          ? stream.cache_health
          : null;
  const cacheScope = typeof summary?.cache_scope === "string" ? summary.cache_scope : typeof stream?.cache_scope === "string" ? stream.cache_scope : null;
  const fallbackReason =
    typeof summary?.fallback_reason === "string"
      ? summary.fallback_reason
      : typeof sourceDetail?.fallback_reason === "string"
        ? sourceDetail.fallback_reason
        : null;
  const staleReason =
    typeof summary?.stale_reason === "string"
      ? summary.stale_reason
      : typeof sourceDetail?.stale_reason === "string"
        ? sourceDetail.stale_reason
        : null;
  const fallbackActive =
    typeof summary?.fallback_active === "boolean"
      ? summary.fallback_active
      : typeof summary?.used_fallback === "boolean"
        ? summary.used_fallback
        : sourceDetail?.fallback_active === true || sourceDetail?.used_fallback === true;
  const redisRequired = typeof summary?.redis_required === "boolean" ? summary.redis_required : stream?.redis_required === true;
  const redisConfigured =
    typeof summary?.redis_configured === "boolean" ? summary.redis_configured : stream?.redis_configured === true;
  const redisConnected =
    typeof summary?.redis_connected === "boolean"
      ? summary.redis_connected
      : typeof stream?.redis_connected === "boolean"
        ? stream.redis_connected
        : null;
  const age = asFiniteNumber(summary?.age_seconds ?? summary?.snapshot_age_seconds);
  const staleAfter = asFiniteNumber(summary?.stale_after_seconds);

  return {
    sourceLabel: marketDataSourceLabel(activeSnapshotSource),
    sourceHint:
      [
        source && source !== activeSnapshotSource ? `응답 원본 소스 ${marketDataSourceLabel(source)} (원본: ${source})` : null,
        fallbackActive ? "REST 보조 경로 사용 중" : "보조 경로 미사용",
        fallbackReason ? `보조 경로 사유 ${marketReasonCodeLabel(fallbackReason)} (원본: ${fallbackReason})` : null,
        staleReason ? `오래된 이유 ${marketReasonCodeLabel(staleReason)} (원본: ${staleReason})` : null,
      ]
        .filter(Boolean)
        .join(" / ") || "서버 시장 데이터 요약 기준",
    statusLabel: marketDataStatusLabel(status ?? sourceStatus),
    statusHint:
      [
        snapshotAt ? `스냅샷 ${formatDateTime(snapshotAt)}` : null,
        sourceTime ? `소스 시각 ${formatDateTime(sourceTime)}` : null,
        receivedAt ? `수신 ${formatDateTime(receivedAt)}` : null,
        age !== null ? `${Math.round(age)}초 경과` : null,
        staleAfter !== null ? `오래됨 기준 ${Math.round(staleAfter)}초` : null,
      ]
        .filter(Boolean)
        .join(" / ") || "서버 시장 데이터 상태 요약 기준",
    streamLabel: marketDataStatusLabel(streamStatus),
    streamHint:
      [
        configuredCacheBackend ? `설정 캐시 ${marketDataSourceLabel(configuredCacheBackend)} (원본: ${configuredCacheBackend})` : null,
        cacheHealth ? `캐시 상태 ${marketDataStatusLabel(cacheHealth)} (원본: ${cacheHealth})` : null,
        cacheScope ? `범위 ${marketContextValueLabel(cacheScope)} (원본: ${cacheScope})` : null,
        redisConfigured ? `Redis 연결 ${redisConnected === true ? "정상" : redisConnected === false ? "끊김" : "확인 필요"}` : null,
        redisRequired ? "Redis 필수 사용" : "Redis 선택 사용",
        streamReason ? `사유 ${marketReasonCodeLabel(streamReason)} (원본: ${streamReason})` : null,
      ]
        .filter(Boolean)
        .join(" / ") || "서버 실시간 스트림 상태 기준",
  };
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

function RiskAIUsageSummary({ usage }: { usage: AIUsagePayload | null | undefined }) {
  if (!usage) {
    return null;
  }
  const today = usage.ai_usage_summary_today_kst;
  const summary7d = usage.ai_usage_summary_7d;
  const summary30d = usage.ai_usage_summary_30d;
  const costEfficiency = usage.ai_cost_efficiency_summary;
  const roleBudget = usage.ai_protection_status?.role_budgets?.trading_decision;
  const tokens24h = roleBudget?.tokens_24h ?? usage.recent_ai_tokens_24h.total_tokens ?? 0;
  const tokenBudget24h = roleBudget?.max_tokens_24h ?? null;
  const budgetLabel = roleBudget?.status === "blocked" ? "차단" : roleBudget?.status === "limited" ? "제한" : "정상";
  const budgetHint = [
    roleBudget?.reason ? `사유 ${formatDisplayValue(roleBudget.reason)}` : null,
    tokenBudget24h !== null ? `24시간 ${formatCount(tokens24h)} / ${formatCount(tokenBudget24h)} tokens` : null,
    roleBudget?.retry_after_seconds ? `재시도 ${formatCount(roleBudget.retry_after_seconds)}초 후` : null,
  ]
    .filter(Boolean)
    .join(" / ");
  const downstream7d = summary7d?.downstream;
  const actionability7d = summary7d?.actionability ?? {};
  const roi7d = summary7d?.roi ?? {};
  const roi30d = summary30d?.roi ?? {};
  const advisor = summary7d?.role_efficiency?.market_settings_advisor?.advisor;
  const advisorCalls = summary7d?.role_efficiency?.market_settings_advisor?.provider_calls ?? 0;
  const holdCount7d = summary7d?.decision_counts?.hold ?? 0;
  const wasteSignalCount = costEfficiency?.waste_assessment?.signals?.length ?? 0;
  const runtimeGuard = costEfficiency?.waste_assessment?.runtime_guard;
  const runtimeGuardActive = runtimeGuard?.status === "active";
  const runtimeGuardMissingForWaste =
    costEfficiency?.waste_assessment?.status === "needs_review" && !runtimeGuardActive;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">AI 비용/억제 상태</p>
          <h2 className="mt-2 text-xl font-semibold text-slate-950">7일 기준 AI 비용/전환 요약</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            24시간 체결 없음은 단독 경고 조건으로 쓰지 않고, role별 비용과 주문/체결 전환을 함께 봅니다.
          </p>
        </div>
        <span
          className={`w-fit rounded-md border px-3 py-1 text-xs font-semibold ${badgeClass(
            roleBudget?.status === "blocked" ? "danger" : roleBudget?.status === "limited" ? "warn" : "good",
          )}`}
        >
          trading_decision {budgetLabel}
        </span>
      </div>
      <div className="grid gap-3 lg:grid-cols-4">
        {metricCard(
          "7일 OpenAI 호출",
          `${formatCount(summary7d?.ai_calls_provider_invoked ?? usage.recent_ai_calls_7d)}회`,
          `성공 ${formatCount(usage.recent_ai_successes_7d)} / 실패 ${formatCount(usage.recent_ai_failures_7d)}`,
          { compact: true },
        )}
        {metricCard(
          "7일 주문 전환",
          formatRatio(actionability7d.provider_to_order_rate),
          `risk 승인 ${formatRatio(actionability7d.provider_to_risk_allowed_rate)} / 체결 ${formatRatio(
            actionability7d.provider_to_fill_rate,
          )}`,
          { compact: true },
        )}
        {metricCard(
          "7일 AI 순기여",
          formatUsdValue(roi7d.net_after_known_ai_cost_usd),
          `체결 순손익 ${formatUsdValue(roi7d.trade_net_realized_pnl)} - AI 비용 ${formatUsdValue(
            roi7d.known_ai_cost_usd,
          )}`,
          { compact: true },
        )}
        {metricCard(
          "월간 비용 추정",
          formatUsdValue(usage.observed_monthly_ai_cost_projection_usd),
          `월간 순효과 ${formatUsdValue(usage.observed_monthly_ai_net_projection_usd)} / 30일 호출 ${formatCount(
            usage.recent_ai_calls_30d,
          )}회`,
          { compact: true },
        )}
      </div>
      {runtimeGuardActive ? (
        <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          <p className="font-semibold text-red-900">신규 진입 AI 비용 게이트 활성</p>
          <p className="mt-1 leading-6">
            비수동 신규 진입 AI 호출은 {aiSkipReasonTitle(runtimeGuard?.reason ?? "low_actionability_cost_guard_active")} 상태로
            차단 중입니다. 보호, 축소, 청산 경로는 이 신규 진입 차단과 분리됩니다.
          </p>
        </div>
      ) : runtimeGuardMissingForWaste ? (
        <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          <p className="font-semibold text-red-900">신규 진입 AI 비용 게이트 미확인</p>
          <p className="mt-1 leading-6">
            비용 낭비 신호가 있지만 런타임 payload에서 비수동 신규 진입 AI 차단 게이트가 활성 상태로 확인되지
            않습니다. 제품화 전 백엔드 런타임 반영 여부를 먼저 증명해야 합니다.
          </p>
        </div>
      ) : null}
      <div className="mt-3 grid gap-3 lg:grid-cols-4">
        {metricCard(
          "7일 risk checks",
          `${formatCount(downstream7d?.risk_checks ?? 0)}건`,
          `허용 ${formatCount(downstream7d?.risk_allowed ?? 0)} / 차단 ${formatCount(
            downstream7d?.risk_blocked ?? 0,
          )}`,
          { compact: true },
        )}
        {metricCard(
          "7일 주문/체결",
          `${formatCount(downstream7d?.orders ?? 0)} / ${formatCount(downstream7d?.fills ?? 0)}`,
          "AI 판단 이후 실제 주문과 체결로 이어진 건수",
          { compact: true },
        )}
        {metricCard(
          "advisor 재사용 신호",
          advisor?.reuse_signal ? "확인 필요" : "반복 낮음",
          `호출 ${formatCount(advisorCalls)} / profile 변경 ${formatCount(
            advisor?.profile_change_count ?? 0,
          )} / 반복 추천 ${formatCount(advisor?.same_profile_recommendation_count ?? 0)}`,
          { compact: true },
        )}
        {metricCard(
          "30일/경고 보조",
          formatUsdValue(roi30d.net_after_known_ai_cost_usd),
          `HOLD ${formatCount(holdCount7d)}회 / 비용 신호 ${formatCount(wasteSignalCount)}건 / 예산 ${budgetLabel}`,
          { compact: true },
        )}
      </div>
      <p className="mt-3 text-xs leading-5 text-slate-500">
        KST 오늘 보조: provider {formatCount(today?.ai_calls_provider_invoked ?? 0)}회, 비용{" "}
        {formatUsdValue(today?.known_estimated_cost_usd)}. {budgetHint || "역할 예산 이상 없음"}
      </p>
    </section>
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

function availableMarketTimeframes(snapshots: Row[], features: Row[]): MarketTimeframeAvailability {
  return resolveAvailableMarketTimeframes(snapshots, features);
}

function candleSourceNote(timeframe: MarketChartTimeframe, sourceTimeframe: string, hasDirectChartCandles: boolean) {
  if (hasDirectChartCandles) {
    return `Binance Futures ${marketTimeframeLabel(timeframe)} 직접 캔들`;
  }
  if (timeframe === sourceTimeframe) {
    return `${marketTimeframeLabel(timeframe)} 저장 스냅샷 캔들`;
  }
  return `${sourceTimeframe} 저장 캔들 집계`;
}

function featureSourceNote(feature: Row | null, timeframe: MarketChartTimeframe) {
  if (!feature) {
    return "지표 없음";
  }
  const direct = rowString(feature, "timeframe");
  if (direct === timeframe) {
    return `${marketTimeframeLabel(timeframe)} 독립 feature row`;
  }
  if (direct === "15m" && timeframe !== "15m") {
    return `15분 기준 feature 내 ${marketTimeframeLabel(timeframe)} multi_timeframe 컨텍스트`;
  }
  return `${direct ?? "unknown"} feature row 기준`;
}

function translateMarketChartApiStatus(value: string | null | undefined, kind: MarketChartEventMarkerKind) {
  const normalized = value?.trim().toLowerCase();
  const labels: Record<string, string> = {
    "ai recommendation": "AI 추천",
    "risk blocked": "리스크 차단",
    "risk approved": "리스크 승인",
    execution: "실제 실행",
  };
  if (normalized && labels[normalized]) {
    return labels[normalized];
  }
  return marketEventMarkerTooltipLabel(kind);
}

function translateMarketChartApiLabel(value: string | null | undefined, kind: MarketChartEventMarkerKind) {
  const normalized = value?.trim().toLowerCase();
  const labels: Record<string, string> = {
    ai: "AI",
    blocked: "차단",
    approved: "승인",
    fill: "체결",
    order: "주문",
  };
  if (normalized && labels[normalized]) {
    return labels[normalized];
  }
  return value || marketEventMarkerTooltipLabel(kind);
}

function translateMarketChartApiAction(value: string | null | undefined, kind: MarketChartEventMarkerKind) {
  if (!value) {
    return "-";
  }
  if (kind === "ai" || kind === "risk_approved" || kind === "risk_blocked") {
    const translated = marketDecisionLabel(value);
    return translated === "알 수 없는 판단" ? formatInternalCodeLabel(value) : translated;
  }
  return value;
}

function translateMarketChartApiDetail(marker: MarketChartEventMarker, action: string, statusLabel: string) {
  if (marker.kind === "ai") {
    const confidence = marker.detail.match(/confidence\s+([0-9.]+)/i)?.[1];
    return confidence ? `${action} / 신뢰도 ${(Number(confidence) * 100).toFixed(0)}%` : action;
  }
  if (marker.kind === "risk_blocked" || marker.kind === "risk_approved") {
    return `${action} ${statusLabel}`;
  }
  return marker.detail;
}

function translateMarketChartApiMarker(marker: MarketChartEventMarker): MarketChartEventMarker {
  const statusLabel = translateMarketChartApiStatus(marker.statusLabel, marker.kind);
  const action = translateMarketChartApiAction(marker.action, marker.kind);
  const reasonLabel =
    marker.reasonCodes && marker.reasonCodes.length > 0
      ? formatMarketReasonCodeDebugLabels(marker.reasonCodes)
      : marker.reasonLabel;
  return {
    ...marker,
    label: translateMarketChartApiLabel(marker.label, marker.kind),
    detail: translateMarketChartApiDetail(marker, action, statusLabel),
    action,
    statusLabel,
    reasonLabel,
  };
}

function buildMarketChartEventMarkers(
  symbol: OperatorSymbol,
  chartMarkers: MarketChartEventMarker[] = [],
): MarketChartEventMarker[] {
  const markers: MarketChartEventMarker[] = [];
  const rowMarkers = chartMarkers
    .filter((marker) => marker.symbol.toUpperCase() === symbol.symbol.toUpperCase())
    .map(translateMarketChartApiMarker);
  const aiAt = symbol.ai_decision.last_ai_invoked_at ?? symbol.ai_decision.created_at;
  if (aiAt && isMarketMarkerDecision(symbol.ai_decision.decision)) {
    markers.push({
      timestamp: aiAt,
      kind: "ai",
      label: "AI",
      detail: `${marketDecisionLabel(symbol.ai_decision.decision)} / ${aiReviewTypeLabel(symbol)}`,
      symbol: symbol.symbol,
      action: marketDecisionLabel(symbol.ai_decision.decision),
      price: null,
      statusLabel: "AI 추천",
      reasonLabel: null,
      sourceId: symbol.ai_decision.decision_run_id ? `ai:${symbol.ai_decision.decision_run_id}` : null,
    });
  }
  if (
    symbol.risk_guard.allowed !== null &&
    symbol.risk_guard.created_at &&
    (isMarketMarkerDecision(symbol.risk_guard.decision) ||
      symbol.risk_guard.blocked_reason_codes.some((code) => code !== "HOLD_DECISION"))
  ) {
    const blocked = symbol.risk_guard.allowed !== true;
    markers.push({
      timestamp: symbol.risk_guard.created_at,
      kind: blocked ? "risk_blocked" : "risk_approved",
      label: blocked ? "차단" : "승인",
      detail:
        blocked && symbol.risk_guard.blocked_reason_codes.length > 0
          ? formatMarketReasonCodeDebugLabels(symbol.risk_guard.blocked_reason_codes)
          : blocked
            ? "리스크 차단"
            : "리스크 승인",
      symbol: symbol.symbol,
      action: marketDecisionLabel(symbol.risk_guard.decision),
      price: null,
      statusLabel: blocked ? "리스크 차단" : "리스크 승인",
      reasonLabel: blocked && symbol.risk_guard.blocked_reason_codes.length > 0 ? formatMarketReasonCodeDebugLabels(symbol.risk_guard.blocked_reason_codes) : null,
      sourceId: symbol.risk_guard.risk_check_id ? `risk:${symbol.risk_guard.risk_check_id}` : null,
    });
  }
  if (symbol.execution.order_id && (symbol.execution.execution_created_at ?? symbol.execution.created_at)) {
    markers.push({
      timestamp: symbol.execution.execution_created_at ?? symbol.execution.created_at ?? "",
      kind: "execution",
      label: "주문",
      detail: symbol.execution.execution_status ?? symbol.execution.order_status ?? "주문 기록",
      symbol: symbol.symbol,
      action: [symbol.execution.side, symbol.execution.order_type].filter(Boolean).join(" ") || "주문",
      price: symbol.execution.fill_price ?? symbol.execution.average_fill_price,
      statusLabel: "실제 실행",
      reasonLabel: symbol.execution.reason_codes.length > 0 ? formatTranslatedCodeList(symbol.execution.reason_codes) : null,
      sourceId: symbol.execution.execution_id
        ? `execution:${symbol.execution.execution_id}`
        : symbol.execution.order_id
          ? `order:${symbol.execution.order_id}`
          : null,
    });
  }
  return dedupeMarketChartEventMarkers([...rowMarkers, ...markers.filter((marker) => marker.timestamp.length > 0)]);
}

function buildMarketChartModels(
  symbols: OperatorDashboardPayload["symbols"],
  snapshots: Row[],
  features: Row[],
  candleWindow: CandleWindow,
  timeframe: MarketChartTimeframe,
  chartCandlesBySymbol: MarketChartCandlesBySymbol = {},
  chartZoomRange: MarketChartZoomRange | null = null,
  chartMarkers: MarketChartEventMarker[] = [],
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
      const candleNote = candleSourceNote(timeframe, sourceTimeframe, hasDirectChartCandles);
      return {
        symbol: symbol.symbol,
        window: candleWindow,
        timeframe,
        sourceTimeframe,
        sourceNote: candleNote,
        featureSourceNote: featureSourceNote(feature, timeframe),
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
        blockedReasonDistribution: buildMarketBlockedReasonDistribution({
          symbol: symbol.symbol,
          candles,
          markers: chartMarkers,
          reasonLabeler: marketReasonCodeLabel,
        }),
        symbolState: symbol,
        events: buildMarketChartEventMarkers(symbol, chartMarkers),
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
  return `${marketTimeframeLabel(model.timeframe)} 봉 기준 / ${model.sourceNote}`;
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
      {marketIndicatorCell("조회 캔들 수", `${model.candles.length}개`, marketVisibleCandleWindowHint(model))}
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
  const rows = [`AI 판단: ${marketDecisionLabel(side)} / 신뢰도 ${formatRatio(confidence)}`];

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

function buildMarketCandlestickClientModel(model: MarketChartModel): ClientMarketCandlestickModel {
  const latestClose = model.candles[model.candles.length - 1]?.close;
  const priceDigits = marketPriceDigits(model.latestPrice ?? latestClose);
  return {
    symbol: model.symbol,
    timeframe: model.timeframe,
    latestPrice: model.latestPrice,
    candles: model.candles,
    baseCandleCount: model.baseCandleCount,
    visibleStartIndex: model.visibleStartIndex,
    visibleEndIndex: model.visibleEndIndex,
    stats: model.stats,
    backendVolumeProfile: featureVolumeProfile(model),
    aiTooltipRows: aiChartDecisionRows(model.symbolState, priceDigits),
    volumeProfileTooltipRows: volumeProfileTooltipRows(
      planVolumeProfileDetails(model.symbolState),
      priceDigits,
    ),
    events: model.events,
  };
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

function marketEventMarkerTooltipLabel(kind: MarketChartEventMarkerKind) {
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

function marketEventMarkerTooltipRows(marker: MarketChartEventMarker, priceDigits: number) {
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
    const showAiRows = index === latestIndex || aiMarkerIndexes.has(index);
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
  return chart;
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
  params.set("symbol", selectedSymbol);
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
            {marketDecisionLabel(planSide ?? decision)}
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
  const riskReasons = symbolRiskReasonCodes(symbol);
  const effectiveDecision = symbol.risk_guard.decision ?? symbol.ai_decision.decision;
  const isEntryReady = symbol.risk_guard.allowed === true && isEntryDecision(effectiveDecision);
  const rawRiskReasonCodes = formatMarketRawReasonCodes(riskReasons);
  const rows = [
    {
      label: "시장 조건",
      value: marketContextValueLabel(
        typeof symbol.market_context_summary.primary_regime === "string"
          ? symbol.market_context_summary.primary_regime
          : null,
      ),
      detail: `추세 ${marketContextValueLabel(
        typeof symbol.market_context_summary.trend_alignment === "string"
          ? symbol.market_context_summary.trend_alignment
          : null,
      )} / 거래량 ${marketContextValueLabel(
        typeof symbol.market_context_summary.volume_regime === "string"
          ? symbol.market_context_summary.volume_regime
          : null,
      )}`,
      kind: symbol.feature_input_delayed || symbol.stale_flags.length > 0 ? ("warn" as const) : ("neutral" as const),
      rawCodes: null,
    },
    {
      label: "AI 판단",
      value: `${marketDecisionLabel(symbol.ai_decision.decision)} / ${review.label}`,
      detail: `${review.detail} / ${currentCycle.label}`,
      kind: symbol.ai_decision.decision === "hold" ? ("neutral" as const) : ("good" as const),
      rawCodes: null,
    },
    {
      label: "리스크 상태",
      value: riskOutcome.label,
      detail:
        riskReasons.length > 0
          ? formatMarketReasonCodeLabels(riskReasons)
          : riskOutcome.detail,
      kind: riskOutcome.kind,
      rawCodes: rawRiskReasonCodes,
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
            {row.rawCodes ? (
              <p className="mt-1 text-[11px] leading-5 text-slate-400">{row.rawCodes}</p>
            ) : null}
          </div>
        ))}
      </div>
    </aside>
  );
}

function MarketSymbolComparisonGrid({
  symbols,
  selectedCandleWindow,
  selectedTimeframe,
}: {
  symbols: OperatorDashboardPayload["symbols"];
  selectedCandleWindow: CandleWindow;
  selectedTimeframe: MarketChartTimeframe;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">시장 비교</p>
          <h2 className="mt-2 text-xl font-semibold text-slate-950">전체 심볼 입력 상태</h2>
        </div>
        <span className="w-fit rounded-md bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
          {symbols.length}개 심볼
        </span>
      </div>

      <div className="mt-5 grid gap-4 xl:grid-cols-3">
        {symbols.map((symbol) => {
          const featureInputMissing = symbol.stale_flags.includes("feature_input_missing");
          const featureInputDelayed = symbol.feature_input_delayed;
          return (
            <article key={symbol.symbol} className="rounded-lg border border-slate-200 bg-slate-50 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold text-slate-950">{symbol.symbol}</h3>
                  <p className="mt-1 text-xs text-slate-500">{formatMarketTiming(symbol)}</p>
                </div>
                <Link
                  href={marketChartHref({
                    selectedSymbol: symbol.symbol,
                    candleWindow: selectedCandleWindow,
                    timeframe: selectedTimeframe,
                  })}
                  className="rounded-md border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700 hover:border-blue-200 hover:text-blue-700"
                >
                  열기
                </Link>
              </div>
              <div className="mt-4 grid gap-3">
                {metricCard("현재가", formatNumber(symbol.latest_price), "선택 심볼 기준 최신 가격", { compact: true })}
                {metricCard(
                  "시장 레짐",
                  String(symbol.market_context_summary.primary_regime ?? "-"),
                  `정렬 ${String(symbol.market_context_summary.trend_alignment ?? "-")}`,
                  { compact: true },
                )}
                {metricCard(
                  "입력 상태",
                  featureInputDelayed ? "지연" : symbol.stale_flags.length > 0 ? "주의" : "정상",
                  featureInputDelayed
                    ? `지표 입력 없음, ${symbol.feature_input_delay_minutes ?? "-"}분 경과`
                    : featureInputMissing
                      ? "피처 입력 대기"
                      : symbol.stale_flags.length > 0
                        ? symbol.stale_flags.map(translateMarketInputFlag).join(", ")
                        : "이상 플래그 없음",
                  { compact: true },
                )}
              </div>
            </article>
          );
        })}
      </div>
    </section>
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
  renderAutoRefresh,
  renderCandlestickChart,
}: {
  models: MarketChartModel[];
  selectedSymbol: string;
  selectedCandleWindow: CandleWindow;
  selectedTimeframe: MarketChartTimeframe;
  timeframeAvailability: Map<MarketChartTimeframe, { enabled: boolean; detail: string }>;
  renderAutoRefresh?: MarketAutoRefreshRenderer;
  renderCandlestickChart?: MarketCandlestickRenderer;
}) {
  const latestCandleTime = latestCandleTimestampForModels(models);
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      {renderAutoRefresh?.({ latestCandleTime, timeframe: selectedTimeframe })}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">시장 차트</p>
          <h2 className="mt-2 text-xl font-semibold text-slate-950">{marketTimeframeLabel(selectedTimeframe)} 가격 흐름과 보조 지표</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            캔들은 선택한 봉 기준으로 표시하고, 1시간/4시간 지표는 15분 기준 feature 안의 multi_timeframe 컨텍스트를 사용합니다.
          </p>
        </div>
        <div className="flex flex-col items-start gap-3 lg:items-end">
          <span className="w-fit rounded-md bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
            {models.length > 0 ? `${models.length}개 심볼` : "차트 데이터 없음"}
          </span>
          <div className="flex flex-wrap gap-1 rounded-md border border-slate-200 bg-slate-50 p-1" aria-label="차트 봉 기준">
            {marketChartTimeframeOptions.map((option) => {
              const active = selectedTimeframe === option.value;
              const availability = timeframeAvailability.get(option.value);
              if (!availability?.enabled) {
                return (
                  <span
                    key={option.value}
                    title={availability?.detail ?? "현재 데이터 없음"}
                    className="inline-flex min-h-9 items-center rounded-md px-3 py-1 text-sm font-semibold text-slate-300"
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
                  className={`inline-flex min-h-9 items-center rounded-md px-3 py-1 text-sm font-semibold transition ${
                    active ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-white hover:text-slate-950"
                  }`}
                >
                  {option.label}
                </Link>
              );
            })}
          </div>
          <div className="flex flex-wrap gap-1 rounded-md border border-slate-200 bg-slate-50 p-1" aria-label="차트 표시 봉 수">
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
                  className={`inline-flex min-h-9 items-center rounded-md px-3 py-1 text-sm font-semibold transition ${
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
      ) : (
        <div className="mt-5 grid gap-4">
          {models.map((model) => {
            const visibleMarkerCount = countVisibleMarketChartMarkers(model.events, model.candles);
            const priceDigits = marketPriceDigits(model.latestPrice ?? model.candles[model.candles.length - 1]?.close);
            return (
              <article key={model.symbol} className="rounded-lg border border-slate-200 bg-slate-50 p-4 [contain-intrinsic-size:720px] [content-visibility:auto]">
                <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <h3 className="text-base font-semibold text-slate-950">{model.symbol}</h3>
                    <p className="mt-1 text-xs text-slate-500" title={`캔들: ${model.sourceNote} / 지표: ${model.featureSourceNote}`}>
                      봉 기준 {marketTimeframeLabel(model.timeframe)} / 캔들: {model.sourceNote} / 지표: {model.featureSourceNote} / 스냅샷 {formatDateTime(model.snapshotTime)}
                    </p>
                    <MarketChartStatusBadges model={model} />
                  </div>
                  <div className="text-left sm:text-right">
                    <p className="text-xs font-medium text-slate-500">현재가</p>
                    <p className="mt-1 text-lg font-semibold text-slate-950">
                      {formatNumber(model.latestPrice, priceDigits)}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">현재 봉 {formatDateTime(model.candles[model.candles.length - 1]?.timestamp)}</p>
                  </div>
                </div>
                <MarketChartStatsStrip model={model} />
                <div className="mt-3 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600" data-market-marker-summary="true">
                  {visibleMarkerCount > 0 ? (
                    <span>AI 판단/리스크/실행 위치 {visibleMarkerCount}개 표시</span>
                  ) : (
                    <span data-market-marker-empty="true">표시할 AI 판단/리스크/실행 위치가 없습니다</span>
                  )}
                </div>
                <MarketPeriodDetailGraphs
                  symbol={model.symbol}
                  timeframeLabel={marketTimeframeLabel(model.timeframe)}
                  candleWindowLabel={marketVisibleCandleWindowLabel(model)}
                  candles={model.candles}
                  priceDigits={priceDigits}
                  blockedReasonDistribution={model.blockedReasonDistribution}
                />
                <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
                  {renderCandlestickChart?.(buildMarketCandlestickClientModel(model))}
                  <div className="space-y-4">
                    <MarketChartAiSummary model={model} />
                    <MarketEntryFlowSummary model={model} />
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function translateDecision(value: string | null | undefined) {
  return marketDecisionLabel(value);
}

function isEntryDecision(value: string | null | undefined) {
  return value === "long" || value === "short" || value === "enter_long" || value === "enter_short";
}

function isMarketMarkerDecision(value: string | null | undefined) {
  return value === "long" || value === "short" || value === "enter_long" || value === "enter_short" || value === "reduce" || value === "exit";
}

function translateAiSkipReason(value: string | null | undefined) {
  return aiSkipReasonTitle(value);
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

function holdDiagnosticReadModel(symbol: OperatorSymbol) {
  const diagnostic = asRecord(aiDecisionReadModel(symbol).hold_diagnostic);
  return asNonEmptyString(diagnostic?.status) === "active" ? diagnostic : null;
}

function holdDiagnosticSummary(symbol: OperatorSymbol) {
  const diagnostic = holdDiagnosticReadModel(symbol);
  if (!diagnostic) {
    return null;
  }
  const summaryCode = asNonEmptyString(diagnostic.summary_code);
  const labels: Record<string, string> = {
    no_entry_candidate: "진입 후보가 0개라 AI/리스크 이후가 아니라 후보 단계에서 관망 중입니다.",
    low_edge_hold: "기대값, 파생시장, 선행시장 근거가 중립이라 거래 우위가 부족합니다.",
    pre_ai_skip: "저효용 후보라 provider 호출 전 관망 처리했습니다.",
    hold_context: "신규 진입보다 관망이 유리한 조건으로 판단했습니다.",
  };
  return labels[summaryCode ?? ""] ?? labels.hold_context;
}

function holdDiagnosticReasonCodes(symbol: OperatorSymbol) {
  const diagnostic = holdDiagnosticReadModel(symbol);
  return diagnostic ? asStringArray(diagnostic.reason_codes) : [];
}

function holdDiagnosticMetricText(symbol: OperatorSymbol) {
  const diagnostic = holdDiagnosticReadModel(symbol);
  const metrics = asRecord(diagnostic?.metrics);
  const entryCandidates = asFiniteNumber(metrics?.entry_candidates);
  const threshold = asFiniteNumber(metrics?.entry_score_threshold);
  const slotScore = asFiniteNumber(metrics?.slot_conviction_score);
  const derivatives = asFiniteNumber(metrics?.derivatives_alignment);
  const leadLag = asFiniteNumber(metrics?.lead_lag_alignment);
  const parts = [
    entryCandidates !== null ? `후보 ${formatCount(entryCandidates)}개` : null,
    threshold !== null ? `기준 ${formatNumber(threshold, 2)}` : null,
    slotScore !== null ? `슬롯 ${formatNumber(slotScore, 2)}` : null,
    derivatives !== null ? `파생 ${formatNumber(derivatives, 2)}` : null,
    leadLag !== null ? `선행 ${formatNumber(leadLag, 2)}` : null,
  ].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(" / ") : "세부 점수 없음";
}

function aiMacroEventContext(symbol: OperatorSymbol) {
  const ai = aiDecisionReadModel(symbol);
  return (
    asMacroEventContextSummary(ai.macro_event_risk_summary) ??
    asMacroEventContextSummary(ai.macro_event_context_summary)
  );
}

function aiSceneReview(symbol: OperatorSymbol) {
  return (
    asRecord(symbol.ai_decision.psychology_scene_review) ??
    asRecord(symbol.pending_entry_plan?.psychology_scene_review)
  );
}

function aiExitReview(symbol: OperatorSymbol) {
  return asRecord(symbol.open_position.position_exit_review);
}

function aiScenePerformance(symbol: OperatorSymbol) {
  return asRecord(symbol.ai_decision.psychology_scene_performance);
}

function sceneReviewCodeLabel(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    bullish: "상승 심리",
    bearish: "하락 심리",
    two_way: "양방향 긴장",
    crowded_long: "롱 쏠림",
    crowded_short: "숏 쏠림",
    uncertain: "불명확",
    trend_pullback: "추세 눌림목",
    breakout_attempt: "돌파 시도",
    range_fade: "박스권 되돌림",
    liquidity_sweep: "유동성 회수",
    trap_risk: "함정 리스크",
    late_chase: "늦은 추격",
    event_reaction: "이벤트 반응",
    position_management: "포지션 관리",
    no_clear_scene: "명확한 장면 없음",
    unknown: "알 수 없음",
    none: "대기",
    watch_zone: "구간 감시",
    wait_1m_confirm: "1분봉 확인 대기",
    avoid_chase: "추격 회피",
    immediate_only_if_risk_guard_allows: "리스크 통과 시에만 즉시",
    manage_existing_position: "보유 포지션 관리",
    metadata_only_no_order_authority: "해석 전용 / 주문 권한 없음",
    no_action: "조치 없음",
    hold_runner: "러너 유지",
    full_take_profit: "전량 익절 검토",
    partial_take_profit: "부분익절 검토",
    tighten_trailing: "트레일링 조임 검토",
    move_to_breakeven: "본전 손절 이동 검토",
    reduce_risk_only: "리스크 축소 검토",
    take_partial_profit: "부분익절 검토",
    reduce_runner: "러너 축소 검토",
    take_profit_exit: "익절 종료 검토",
    stand_aside: "관망",
    defer_to_existing_plan: "기존 계획 우선",
    protect_unrealized: "미실현 수익 보호",
    let_runner_work: "러너 지속",
    not_applicable: "해당 없음",
    healthy: "러너 양호",
    extended: "러너 확장",
    fragile: "러너 취약",
    exhausted: "러너 소진",
    watch: "감시",
    soon: "가까운 시점",
    now: "즉시 검토",
    triggered_with_fill: "체결까지 연결",
    triggered_order_no_fill: "주문 제출, 체결 미확인",
    triggered_no_order_observed: "트리거됨, 주문 미확인",
    canceled_invalidated: "무효화 취소",
    waiting_confirm_quality_low: "1분봉 확인 품질 부족",
    canceled: "취소",
    expired: "만료",
    armed_waiting: "대기 중",
    no_pending_plan: "대기 플랜 없음",
  };
  return labels[value] ?? formatInternalCodeLabel(value);
}

function sceneReviewText(review: Record<string, unknown>, key: string, fallbackCodeKey?: string) {
  return (
    asNonEmptyString(review[key]) ??
    sceneReviewCodeLabel(asNonEmptyString(fallbackCodeKey ? review[fallbackCodeKey] : null))
  );
}

function AiSceneReviewPanel({ symbol }: { symbol: OperatorSymbol }) {
  const review = aiSceneReview(symbol);
  if (!review) {
    return null;
  }
  const performance = aiScenePerformance(symbol);
  const confirmationCues = asStringArray(review.confirmation_cues);
  const invalidationCues = asStringArray(review.invalidation_cues);
  const reasonCodes = asStringArray(review.reason_codes);
  const performanceReasons = asStringArray(performance?.reason_codes);
  const summary = asNonEmptyString(review.summary);
  const boundary = sceneReviewCodeLabel(
    asNonEmptyString(review.execution_boundary) ?? "metadata_only_no_order_authority",
  );
  const outcomeBucket = asNonEmptyString(performance?.outcome_bucket);
  const pendingPlanCount = asFiniteNumber(performance?.pending_plan_count) ?? 0;
  const orderCount = asFiniteNumber(performance?.order_count) ?? 0;
  const fillCount = asFiniteNumber(performance?.fill_count) ?? 0;
  const lowQualityCount = asFiniteNumber(performance?.plan_confirm_quality_low_count) ?? 0;
  const invalidatedCount = asFiniteNumber(performance?.plan_invalidated_count) ?? 0;
  const hasPerformance =
    performance !== null &&
    Object.keys(performance).length > 0 &&
    Boolean(outcomeBucket || pendingPlanCount || orderCount || fillCount || lowQualityCount || invalidatedCount);

  return (
    <div className="rounded-md border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-slate-500">AI 장면 해석</p>
          <h3 className="mt-1 text-base font-semibold text-slate-950">
            {sceneReviewText(review, "scene_scenario", "scene_type")}
          </h3>
          {summary ? <p className="mt-2 text-sm leading-6 text-slate-600">{summary}</p> : null}
        </div>
        <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass("neutral")}`}>
          {boundary}
        </span>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <div>
          <p className="text-xs font-medium text-slate-500">시장 심리</p>
          <p className="mt-2 text-sm font-semibold leading-6 text-slate-950">
            {sceneReviewText(review, "market_psychology", "psychology_bias")}
          </p>
          <p className="mt-1 text-xs text-slate-500">{sceneReviewCodeLabel(asNonEmptyString(review.psychology_bias))}</p>
        </div>
        <div>
          <p className="text-xs font-medium text-slate-500">진입 연출</p>
          <p className="mt-2 text-sm font-semibold leading-6 text-slate-950">
            {sceneReviewText(review, "entry_choreography", "preferred_entry_timing")}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {sceneReviewCodeLabel(asNonEmptyString(review.preferred_entry_timing))}
          </p>
        </div>
        <div>
          <p className="text-xs font-medium text-slate-500">장면 코드</p>
          <p className="mt-2 text-sm font-semibold leading-6 text-slate-950">
            {sceneReviewCodeLabel(asNonEmptyString(review.scene_type))}
          </p>
          <p className="mt-1 text-xs text-slate-500">{formatTranslatedCodeList(reasonCodes)}</p>
        </div>
      </div>
      {confirmationCues.length > 0 || invalidationCues.length > 0 ? (
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <div>
            <p className="text-xs font-medium text-slate-500">확인 단서</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {confirmationCues.map((cue) => (
                <span key={cue} className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
                  {cue}
                </span>
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs font-medium text-slate-500">무효화 단서</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {invalidationCues.map((cue) => (
                <span key={cue} className="rounded-full bg-rose-50 px-3 py-1 text-xs font-medium text-rose-700">
                  {cue}
                </span>
              ))}
            </div>
          </div>
        </div>
      ) : null}
      {hasPerformance ? (
        <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3">
          <div className="grid gap-3 md:grid-cols-3">
            <div>
              <p className="text-xs font-medium text-slate-500">성과 추적</p>
              <p className="mt-1 text-sm font-semibold text-slate-950">{sceneReviewCodeLabel(outcomeBucket)}</p>
              <p className="mt-1 text-xs text-slate-500">플랜 {pendingPlanCount} / 주문 {orderCount} / 체결 {fillCount}</p>
            </div>
            <div>
              <p className="text-xs font-medium text-slate-500">감시 결과</p>
              <p className="mt-1 text-sm font-semibold text-slate-950">
                품질 부족 {lowQualityCount} / 무효화 {invalidatedCount}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {sceneReviewCodeLabel(asNonEmptyString(performance.latest_plan_status))}
                {asNonEmptyString(performance.latest_canceled_reason)
                  ? ` / ${sceneReviewCodeLabel(asNonEmptyString(performance.latest_canceled_reason))}`
                  : ""}
              </p>
            </div>
            <div>
              <p className="text-xs font-medium text-slate-500">결과 코드</p>
              <p className="mt-1 text-sm font-semibold text-slate-950">{formatTranslatedCodeList(performanceReasons)}</p>
              <p className="mt-1 text-xs text-slate-500">DecisionPerformanceFact 기준 read-only 집계</p>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function AiPositionExitReviewPanel({ symbol }: { symbol: OperatorSymbol }) {
  if (!symbol.open_position.is_open) {
    return null;
  }
  const review = aiExitReview(symbol);
  if (!review) {
    return null;
  }
  const reasonCodes = asStringArray(review.reason_codes);
  const protectionCues = asStringArray(review.profit_protection_cues);
  const invalidationCues = asStringArray(review.runner_invalidation_cues);
  const dataQualityNotes = asStringArray(review.data_quality_notes);
  const summary = asNonEmptyString(review.summary) ?? asNonEmptyString(review.rationale);
  const boundary = sceneReviewCodeLabel(
    asNonEmptyString(review.execution_boundary) ?? "metadata_only_no_order_authority",
  );
  const confidence = asFiniteNumber(review.confidence);

  return (
    <div className="rounded-md border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-slate-500">AI 익절 판단</p>
          <h3 className="mt-1 text-base font-semibold text-slate-950">
            {sceneReviewCodeLabel(asNonEmptyString(review.recommendation))}
          </h3>
          {summary ? <p className="mt-2 text-sm leading-6 text-slate-600">{summary}</p> : null}
        </div>
        <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass("neutral")}`}>
          {boundary}
        </span>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-4">
        <div>
          <p className="text-xs font-medium text-slate-500">익절 성향</p>
          <p className="mt-2 text-sm font-semibold leading-6 text-slate-950">
            {sceneReviewCodeLabel(asNonEmptyString(review.profit_take_bias))}
          </p>
        </div>
        <div>
          <p className="text-xs font-medium text-slate-500">러너 상태</p>
          <p className="mt-2 text-sm font-semibold leading-6 text-slate-950">
            {sceneReviewCodeLabel(asNonEmptyString(review.runner_state))}
          </p>
        </div>
        <div>
          <p className="text-xs font-medium text-slate-500">검토 긴급도</p>
          <p className="mt-2 text-sm font-semibold leading-6 text-slate-950">
            {sceneReviewCodeLabel(asNonEmptyString(review.exit_urgency))}
          </p>
        </div>
        <div>
          <p className="text-xs font-medium text-slate-500">신뢰도</p>
          <p className="mt-2 text-sm font-semibold leading-6 text-slate-950">
            {confidence !== null ? `${Math.round(confidence * 100)}%` : "-"}
          </p>
        </div>
      </div>
      {protectionCues.length > 0 || invalidationCues.length > 0 || dataQualityNotes.length > 0 ? (
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <div>
            <p className="text-xs font-medium text-slate-500">수익 보호 단서</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {protectionCues.length > 0 ? (
                protectionCues.map((cue) => (
                  <span key={cue} className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
                    {cue}
                  </span>
                ))
              ) : (
                <span className="text-xs text-slate-500">-</span>
              )}
            </div>
          </div>
          <div>
            <p className="text-xs font-medium text-slate-500">러너 무효화 단서</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {invalidationCues.length > 0 ? (
                invalidationCues.map((cue) => (
                  <span key={cue} className="rounded-full bg-rose-50 px-3 py-1 text-xs font-medium text-rose-700">
                    {cue}
                  </span>
                ))
              ) : (
                <span className="text-xs text-slate-500">-</span>
              )}
            </div>
          </div>
          <div>
            <p className="text-xs font-medium text-slate-500">데이터 품질</p>
            <p className="mt-2 text-xs leading-5 text-slate-600">
              {dataQualityNotes.length > 0 ? formatTranslatedCodeList(dataQualityNotes) : "특이사항 없음"}
            </p>
          </div>
        </div>
      ) : null}
      <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3">
        <p className="text-xs font-medium text-slate-500">결과 코드</p>
        <p className="mt-1 text-sm font-semibold text-slate-950">{formatTranslatedCodeList(reasonCodes)}</p>
        <p className="mt-1 text-xs text-slate-500">
          AI 단독 주문 권한은 없고, 최신 데이터, 보호 주문, 리스크, 실행 조건을 통과할 때만 후보로 사용됩니다.
        </p>
      </div>
    </div>
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
  const providerStatus = asNonEmptyString(review?.provider_status);
  const currentReviewSkipped = Boolean(
    getAiSkipReason(symbol) ||
      review?.provider_skipped === true ||
      review?.trigger_deduped === true ||
      symbol.ai_decision.trigger_deduped === true ||
      providerStatus === "skipped_pre_ai" ||
      providerStatus === "deduped",
  );
  if (currentReviewSkipped) {
    return false;
  }
  return Boolean(
    review?.provider_invoked === true ||
      providerStatus === "invoked" ||
      review?.invoked_at ||
      (!providerStatus && (ai.last_ai_invoked_at || ai.provider_name)),
  );
}

function buildAiDecisionFlowStages(symbol: OperatorSymbol): TimelineStage[] {
  const ai = aiDecisionReadModel(symbol);
  const review = aiReviewReadModel(symbol);
  const triggerReason = getAiTriggerReason(symbol);
  const triggerPresentation = describeAiTriggerReason(triggerReason);
  const skipReason = getAiSkipReason(symbol);
  const triggerDeduped = review?.trigger_deduped === true || symbol.ai_decision.trigger_deduped === true;
  const riskReasons = symbolRiskReasonCodes(symbol);
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
          title: "리스크",
          label: "승인",
          detail: isEntryDecision(effectiveDecision)
            ? "규칙 기반 리스크 점검에서 신규 진입을 승인했습니다."
            : "규칙 기반 리스크 점검을 통과했습니다.",
          kind: "good",
        }
      : symbol.risk_guard.allowed === false
        ? passiveRiskOnly
          ? {
              key: "risk",
              title: "리스크",
              label: "대기",
              detail: riskReasons.length > 0 ? formatTranslatedCodeList(riskReasons) : "신규 진입 신호가 없습니다.",
              kind: "neutral",
            }
          : {
            key: "risk",
            title: "리스크",
            label: "차단",
            detail: riskReasons.length > 0 ? formatTranslatedCodeList(riskReasons) : "차단 사유 기록 없음",
            kind: "danger",
          }
        : {
            key: "risk",
            title: "리스크",
            label: "미평가",
            detail: "리스크 점검 평가 기록이 없습니다.",
            kind: "neutral",
          };

  const executionStage: TimelineStage = currentExecutionExists
    ? {
          key: "execution",
          title: "실행",
          label: symbol.execution.execution_status === "filled" ? "주문 실행" : "주문 제출",
        detail: symbol.execution.execution_status ?? symbol.execution.order_status ?? "실행 상태 확인 필요",
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
            detail: "리스크 점검 차단으로 신규 주문이 실행되지 않습니다.",
            kind: "danger",
          }
        : symbol.risk_guard.allowed === true && isEntryDecision(effectiveDecision)
          ? {
              key: "execution",
              title: "실행",
              label: "실행 없음",
              detail: "리스크 승인은 주문 체결 기록이 아니며, 주문 또는 대기 진입 계획 기록이 없습니다.",
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
            이벤트, AI 검토 여부, 리스크 승인, 실행/대기 상태를 단계별로 분리해서 봅니다.
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

function EntryLifecycleTabs({
  operator,
  symbol,
  activeTab,
}: {
  operator: OperatorDashboardPayload;
  symbol: OperatorSymbol;
  activeTab: EntryLifecycleTab;
}) {
  const activeTabMeta = entryLifecycleTabs.find((item) => item.key === activeTab) ?? entryLifecycleTabs[0];
  const tabHref = (tab: EntryLifecycleTab) => {
    const params = new URLSearchParams();
    params.set("symbol", symbol.symbol);
    params.set("flow", tab);
    return `/dashboard/decisions?${params.toString()}`;
  };

  return (
    <section className="mt-5 rounded-lg border border-slate-200 bg-slate-50 p-4 sm:p-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-950">진입 플랜 묶음</p>
          <p className="mt-1 text-xs leading-5 text-slate-600">
            {activeTabMeta.description}. 같은 플랜을 여러 카드에 반복하지 않고 계획, 감시, 실행 결과를 나눠 봅니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {entryLifecycleTabs.map((tab) => {
            const active = tab.key === activeTab;
            return (
              <Link
                key={tab.key}
                href={tabHref(tab.key)}
                className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${
                  active
                    ? "border-blue-600 bg-blue-600 text-white"
                    : "border-slate-200 bg-white text-slate-700 hover:border-blue-200 hover:bg-blue-50"
                }`}
              >
                {tab.label}
              </Link>
            );
          })}
        </div>
      </div>

      <div className="mt-4">
        {activeTab === "summary" ? (
          <EntryLifecycleSummaryPanel operator={operator} symbol={symbol} />
        ) : activeTab === "plan" ? (
          <EntryLifecyclePlanPanel symbol={symbol} />
        ) : (
          <EntryLifecycleExecutionPanel symbol={symbol} />
        )}
      </div>
    </section>
  );
}

function EntryLifecycleSummaryPanel({
  operator,
  symbol,
}: {
  operator: OperatorDashboardPayload;
  symbol: OperatorSymbol;
}) {
  const recommendation = summarizeLastAiRecommendation(symbol);
  const review = aiReviewSummary(symbol);
  const currentCycle = summarizeCurrentCycleSelection(symbol);
  const riskOutcome = summarizeRiskGate(symbol);
  const execution = summarizeExecutionState(symbol);
  const positionStatus = positionStatusText(symbol);
  const triggerPresentation = describeAiTriggerReason(getAiTriggerReason(symbol));
  const marketSignalSummary = aiMarketSignalSummary(symbol);
  const macroEventContext = aiMacroEventContext(symbol);
  const macroEventSummary = formatMacroEventContextSummary(macroEventContext);
  const macroEventDetail = formatMacroEventContextDetail(macroEventContext);
  const riskReasons = symbolRiskReasonCodes(symbol);
  const rationaleBadges = summarizeInternalCodeBadges(symbol.ai_decision.rationale_codes);
  const holdDiagnosticText = holdDiagnosticSummary(symbol);
  const holdDiagnosticCodes = holdDiagnosticReasonCodes(symbol);
  const holdDiagnosticBadges = summarizeInternalCodeBadges(holdDiagnosticCodes);

  return (
    <div className="space-y-4">
      <AiDecisionFlowTimeline symbol={symbol} />
      <div className="rounded-md border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(recommendation.kind)}`}>
            {recommendation.label}
          </span>
          <span className="text-xs text-slate-500">{formatDateTime(symbol.ai_decision.created_at)}</span>
        </div>
        <p className="mt-3 text-xl font-semibold text-slate-950">{translateDecision(symbol.ai_decision.decision)}</p>
        <p className="mt-2 text-sm leading-6 text-slate-700">{formatAiExplanation(symbol.ai_decision.explanation_short)}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {rationaleBadges.visible.length > 0 ? (
            <>
              {rationaleBadges.visible.map((badge) => (
                <span
                  key={badge.key}
                  title={badge.codes.join(", ")}
                  className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700"
                >
                  {badge.label}
                  {badge.count > 1 ? ` ${badge.count}개` : ""}
                </span>
              ))}
              {rationaleBadges.hiddenCount > 0 ? (
                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700">
                  외 {rationaleBadges.hiddenCount}개
                </span>
              ) : null}
            </>
          ) : (
            <span className="text-sm text-slate-500">근거 코드 없음</span>
          )}
        </div>
        {holdDiagnosticText ? (
          <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3">
            <p className="text-xs font-semibold text-amber-900">관망 진단</p>
            <p className="mt-1 text-sm leading-6 text-amber-950">{holdDiagnosticText}</p>
            <p className="mt-1 text-xs leading-5 text-amber-800">{holdDiagnosticMetricText(symbol)}</p>
            {holdDiagnosticBadges.visible.length > 0 ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {holdDiagnosticBadges.visible.map((badge) => (
                  <span
                    key={badge.key}
                    title={badge.codes.join(", ")}
                    className="rounded-full bg-white px-3 py-1 text-xs font-medium text-amber-900"
                  >
                    {badge.label}
                    {badge.count > 1 ? ` ${badge.count}개` : ""}
                  </span>
                ))}
                {holdDiagnosticBadges.hiddenCount > 0 ? (
                  <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-amber-900">
                    외 {holdDiagnosticBadges.hiddenCount}개
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
        {rawCodeDetails("AI 내부 근거 보기", symbol.ai_decision.rationale_codes)}
      </div>
      <AiSceneReviewPanel symbol={symbol} />
      <AiPositionExitReviewPanel symbol={symbol} />
      <div className="grid gap-3 lg:grid-cols-3">
        {metricCard("현재 결론", recommendation.detail, currentCycle.detail)}
        {metricCard("AI 검토", review.label, review.detail)}
        {metricCard("리스크 상태", riskOutcome.label, riskReasons.length > 0 ? formatTranslatedCodeList(riskReasons) : riskOutcome.detail)}
        {metricCard("실행 상태", execution.label, execution.detail)}
        {metricCard("포지션 상태", positionStatus.label, positionStatus.detail)}
        {metricCard(
          triggerPresentation.legacy ? "과거 정책 기록" : "검토 트리거",
          triggerPresentation.label,
          triggerPresentation.legacy ? triggerPresentation.hint : `대시보드 기준 ${formatDateTime(operator.generated_at)}`,
          { compact: true },
        )}
        {metricCard("시장 신호 요약", marketSignalSummary, "AI 호출 원인이 아니라 판단 당시 지표 요약입니다.", {
          compact: true,
        })}
        {metricCard("이벤트 리스크", macroEventSummary, macroEventDetail, { compact: true })}
        {metricCard(
          "마지막 AI 스냅샷",
          formatDateTime(symbol.ai_decision.created_at),
          "상단 AI 카드는 과거 스냅샷일 수 있습니다.",
          { compact: true },
        )}
      </div>
    </div>
  );
}

function EntryLifecyclePlanPanel({ symbol }: { symbol: OperatorSymbol }) {
  const planDetails = activeEntryPlanDetails(symbol);
  const plan = symbol.pending_entry_plan;
  const triggerDetails = asRecord(plan?.trigger_details);
  const sourceBlockedReasons = plan?.source_blocked_reason_codes ?? [];
  const rationaleCodes = plan?.rationale_codes ?? [];
  const latestPrice = asFiniteNumber(triggerDetails?.latest_price) ?? symbol.latest_price;
  const observedChaseBps = asFiniteNumber(triggerDetails?.observed_chase_bps);
  const currentExpectedRr = asFiniteNumber(triggerDetails?.current_expected_rr);
  const expectedRrDeterioration = asFiniteNumber(triggerDetails?.expected_rr_deterioration_pct);
  const qualityState = rowString(triggerDetails, "quality_state") ?? "미확인";
  const watchReason = rowString(triggerDetails, "reason") ?? "감시 기록 없음";
  const lateChase = rowBoolean(triggerDetails, "late_chase") ?? false;
  const rrCollapse = rowBoolean(triggerDetails, "rr_collapse") ?? false;
  const zoneEntered = rowBoolean(triggerDetails, "zone_entered");
  const confirmMet = rowBoolean(triggerDetails, "confirm_met");

  if (!plan?.plan_id) {
    return (
      <div className="rounded-md border border-dashed border-slate-300 bg-white px-4 py-8 text-sm text-slate-500">
        <p className="font-semibold text-slate-700">현재 저장된 진입 계획이 없습니다.</p>
        <p className="mt-2 leading-6">새 판단에서 watch entry plan이 승인되면 이 탭에 계획부터 감시 상태까지 묶어서 표시됩니다.</p>
      </div>
    );
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
      <div className="rounded-md border border-slate-200 bg-white p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">계획 #{plan.plan_id}</p>
            <h3 className="mt-1 text-lg font-semibold text-slate-950">{planDetails.label}</h3>
            <p className="mt-2 text-sm leading-6 text-slate-600">{planDetails.detail}</p>
          </div>
          <span className={`w-fit rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(planDetails.kind)}`}>
            {formatInternalCodeLabel(plan.plan_status)}
          </span>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {metricCard("진입 방향", formatPlanSide(plan.side), `진입 방식 ${formatInternalCodeLabel(plan.entry_mode)}`)}
          {metricCard("진입 구간", planDetails.zoneText, planDetails.distanceText)}
          {metricCard("현재가", formatPriceValue(latestPrice), `최대 추격 ${formatNumber(plan.max_chase_bps, 2)} bps`)}
          {metricCard("무효화 / 만료", `${planDetails.invalidationText} / ${planDetails.expiresText}`, "구간 감시 관리 기준", {
            compact: true,
          })}
          {metricCard(
            "손절 / 목표",
            `${formatPriceValue(plan.stop_loss ?? plan.invalidation_price)} / ${formatPriceValue(plan.take_profit)}`,
            "계획 생성 시 저장된 보호 기준",
          )}
          {metricCard(
            "리스크 상한",
            `${formatRatio(plan.risk_pct_cap)} / ${formatNumber(plan.leverage_cap, 2)}x`,
            "최종 주문 전 리스크 점검에서 다시 검증",
          )}
          {metricCard(
            "생성 근거",
            plan.source_decision_run_id !== null ? `판단 #${plan.source_decision_run_id}` : "연결된 판단 없음",
            plan.source_risk_check_id ? `생성 시 리스크 #${plan.source_risk_check_id}` : "연결된 리스크 없음",
            { compact: true },
          )}
          {metricCard("마지막 감시", formatDateTime(plan.last_watch_at), `스냅샷 ${plan.last_watch_snapshot_id ?? "-"}`, {
            compact: true,
          })}
        </div>

        {sourceBlockedReasons.length > 0 ? (
          <div className="mt-4">
            <p className="mb-2 text-xs font-medium text-slate-500">계획 생성 시 차단 사유</p>
            <RiskReasonGroups reasons={sourceBlockedReasons} />
          </div>
        ) : null}
        {rawCodeDetails("계획 내부 근거 보기", rationaleCodes)}
      </div>

      <div className="rounded-md border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">감시 상태</p>
            <h3 className="mt-1 text-base font-semibold text-slate-950">{formatInternalCodeLabel(watchReason)}</h3>
          </div>
          <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(zoneEntered ? "good" : "warn")}`}>
            {zoneEntered ? "구간 도달" : "구간 대기"}
          </span>
        </div>
        <div className="mt-4 grid gap-3">
          {metricCard("품질 상태", formatInternalCodeLabel(qualityState), entryPlanQualityScoreText(triggerDetails), {
            compact: true,
          })}
          {metricCard(
            "확인 조건",
            `${confirmMet ? "충족" : "대기"} / ${zoneEntered ? "구간 안" : "구간 밖"}`,
            "구간 도달과 확인 조건이 같이 맞아야 다음 단계로 갑니다.",
            { compact: true },
          )}
          {metricCard(
            "추격 폭",
            observedChaseBps !== null ? `${formatNumber(observedChaseBps, 2)} bps` : "-",
            lateChase ? "늦은 추격으로 감시 대기 또는 차단될 수 있습니다." : "추격 폭 기록",
            { compact: true },
          )}
          {metricCard(
            "현재 기대 R/R",
            formatNumber(currentExpectedRr, 3),
            expectedRrDeterioration !== null ? `초기 대비 악화 ${formatRatio(expectedRrDeterioration)}` : "R/R 악화 기록 없음",
            { compact: true },
          )}
          {metricCard("R/R 붕괴", rrCollapse ? "예" : "아니오", "기대 보상 대비 진입 비용 악화 여부", { compact: true })}
        </div>
        {triggerDetails ? (
          <details className="mt-4 rounded-md border border-slate-200 bg-slate-50">
            <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-slate-800">
              감시 원본 보기
            </summary>
            <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words border-t border-slate-200 p-4 text-xs leading-6 text-slate-700">
              {JSON.stringify(triggerDetails, null, 2)}
            </pre>
          </details>
        ) : null}
      </div>
    </div>
  );
}

function EntryLifecycleExecutionPanel({ symbol }: { symbol: OperatorSymbol }) {
  const execution = summarizeExecutionState(symbol);
  const recentExecution = summarizeRecentExecutionRecord(symbol);
  const pendingPlan = pendingEntryPlanPresentation(symbol);
  const protectionReview = protectionReviewPresentation(symbol);
  const positionStatus = positionStatusText(symbol);
  const fills = symbol.execution.recent_fills ?? [];
  const currentExecutionExists = symbol.execution.order_id !== null && !isHistoricalExecutionRecord(symbol);

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
      <div className="rounded-md border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">주문 / 체결</p>
            <h3 className="mt-1 text-lg font-semibold text-slate-950">{execution.label}</h3>
          </div>
          <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(execution.kind)}`}>
            {currentExecutionExists ? "현재 실행 기록" : "실행 없음"}
          </span>
        </div>
        <p className="mt-2 text-sm leading-6 text-slate-600">{execution.detail}</p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {metricCard(
            "주문 ID",
            symbol.execution.order_id !== null ? String(symbol.execution.order_id) : "-",
            symbol.execution.decision_run_id !== null ? `decision #${symbol.execution.decision_run_id}` : "연결된 decision 없음",
            { compact: true },
          )}
          {metricCard(
            "주문 상태",
            formatDisplayValue(symbol.execution.order_status ?? symbol.execution.execution_status ?? "-", "status"),
            `유형 ${formatDisplayValue(symbol.execution.order_type ?? "-", "order_type")}`,
          )}
          {metricCard(
            "요청 / 체결 수량",
            `${formatNumber(symbol.execution.requested_quantity, 6)} / ${formatNumber(symbol.execution.filled_quantity, 6)}`,
            "실제 체결 수량은 execution row 기준",
          )}
          {metricCard(
            "평균 / 체결가",
            `${formatPriceValue(symbol.execution.average_fill_price)} / ${formatPriceValue(symbol.execution.fill_price)}`,
            "평균 체결가와 최근 fill 가격",
          )}
          {metricCard(
            "생성 / 체결 시각",
            `${formatDateTime(symbol.execution.created_at)} / ${formatDateTime(symbol.execution.execution_created_at)}`,
            "주문과 체결 기록 시각",
            { compact: true },
          )}
          {recentExecution
            ? metricCard("최근 과거 실행 기록", recentExecution.label, recentExecution.detail, { compact: true })
            : metricCard("최근 과거 실행 기록", "-", "현재 심볼의 과거 실행 기록이 없습니다.", { compact: true })}
        </div>
        {symbol.execution.reason_codes.length > 0 ? (
          <div className="mt-4">
            <p className="mb-2 text-xs font-medium text-slate-500">실행 사유</p>
            <RiskReasonGroups reasons={symbol.execution.reason_codes} />
          </div>
        ) : null}
      </div>

      <div className="rounded-md border border-slate-200 bg-white p-4">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">결과 확인</p>
        <div className="mt-4 grid gap-3">
          {metricCard("진입 대기 플랜", pendingPlan.label, pendingPlan.detail, { compact: true })}
          {metricCard("보호 주문 점검", protectionReview.label, protectionReview.detail, { compact: true })}
          {metricCard("하드 스탑", hardStopLabel(symbol), `손절 확대 ${stopWideningLabel(symbol)}`, { compact: true })}
          {metricCard("오픈 포지션", positionStatus.label, positionStatus.detail, { compact: true })}
        </div>
        {pendingPlan.planActive ? (
          <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900">
            조건부 진입 대기입니다. 구간 도달 후 AI 재판단과 리스크 승인을 다시 통과해야 주문 실행 단계로 넘어갑니다.
          </div>
        ) : null}
      </div>

      <div className="xl:col-span-2 rounded-md border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">최근 체결</p>
            <h3 className="mt-1 text-base font-semibold text-slate-950">fill 기록</h3>
          </div>
          <span className="rounded-md bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">{fills.length}건</span>
        </div>
        {fills.length === 0 ? (
          <div className="mt-4 rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-sm text-slate-500">
            이 계획 또는 심볼에 연결된 최근 체결 기록이 없습니다.
          </div>
        ) : (
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {fills.map((fill, index) => (
              <div key={`${fill.execution_id ?? "fill"}-${index}`} className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
                <p className="text-xs font-semibold text-slate-500">{formatDateTime(fill.created_at)}</p>
                <p className="mt-2 text-sm font-semibold text-slate-950">
                  {formatNumber(fill.fill_quantity, 6)} @ {formatPriceValue(fill.fill_price)}
                </p>
                <p className="mt-1 text-xs text-slate-600">
                  수수료 {formatNumber(fill.fee_paid, 4)} {fill.commission_asset ?? ""}
                </p>
                <p className="mt-1 text-xs text-slate-500">상태 {formatDisplayValue(fill.status ?? "-", "status")}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function classifyRiskReasonGroup(value: string, allReasonCodes: string[] | null | undefined): RiskReasonGroupKey {
  const normalized = value.trim();
  const upper = normalized.toUpperCase();
  const lower = normalized.toLowerCase();

  if (isEntryWaitReasonCodeInContext(normalized, allReasonCodes)) {
    return "trigger";
  }

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
    upper.includes("MACRO_EVENT") ||
    upper.includes("MACRO_RELEASE") ||
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
  const contextReasons = values?.filter((value) => value.trim().length > 0) ?? [];
  for (const value of values ?? []) {
    const reason = typeof value === "string" ? value.trim() : "";
    if (!reason || seen.has(reason)) {
      continue;
    }
    seen.add(reason);
    const group = classifyRiskReasonGroup(reason, contextReasons);
    groups.set(group, [...(groups.get(group) ?? []), reason]);
  }
  return riskReasonGroupOrder
    .map((key) => ({ key, reasons: groups.get(key) ?? [] }))
    .filter((item) => item.reasons.length > 0);
}

function reasonCategoryBadge(category: ReasonCodeCategory) {
  if (category === "entry_wait") return "진입 대기";
  if (category === "operational_control") return "운영 제어";
  if (category === "safety_block") return "안전 차단";
  return "확인 필요";
}

function reasonCategoryTone(category: ReasonCodeCategory) {
  if (category === "entry_wait") return "warn" as const;
  if (category === "operational_control" || category === "safety_block") return "danger" as const;
  return "neutral" as const;
}

function RiskReasonGroups({
  reasons,
  emptyText = "차단 사유 없음",
}: {
  reasons: string[] | null | undefined;
  emptyText?: string;
}) {
  const groups = groupedRiskReasons(reasons);
  const contextReasons = reasons?.filter((reason) => reason.trim().length > 0) ?? [];
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
            {group.reasons.map((reason) => {
              const copy = describeReasonCodeInContext(reason, contextReasons);
              return (
                <div key={reason} className="rounded-md bg-slate-50 px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-medium text-slate-800">{copy.title_ko}</p>
                    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${badgeClass(reasonCategoryTone(copy.category))}`}>
                      {reasonCategoryBadge(copy.category)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-slate-600">{copy.detail_ko}</p>
                  <p className="mt-1 text-xs leading-5 text-slate-500">자동 해소: {copy.auto_clear_hint_ko}</p>
                  <p className="mt-1 text-xs leading-5 text-slate-500">확인 위치: {copy.check_location_ko}</p>
                  <p className="mt-1 break-all font-mono text-[11px] text-slate-500">raw: {copy.raw_code}</p>
                </div>
              );
            })}
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
      detail: "현재 저장된 대기 진입 계획이 없습니다.",
      kind: "neutral" as const,
      planActive: false,
    };
  }

  const mode = formatInternalCodeLabel(plan.entry_mode);
  const planRecord = asRecord(plan);
  const watcherSummary = entryPlanWatcherSummary(planRecord);
  if (plan.plan_status === "armed") {
    const confirmation = entryPlanConfirmationPresentation(asRecord(plan.trigger_details), formatPlanSide(plan.side));
    return {
      label: confirmation?.label ?? "조건부 진입 대기",
      detail:
        confirmation?.detail ??
        `AI 판단은 생성되었지만 즉시 주문이 아닙니다. ${mode} 조건 충족 후 리스크/실행 재검증이 필요합니다.`,
      kind: "warn" as const,
      planActive: true,
    };
  }
  if (plan.plan_status === "triggered") {
    return {
      label: "조건 확인 후 실행 경로",
      detail: "대기 진입 계획 조건이 충족된 기록입니다. 실제 주문/체결 여부는 실행 상태에서 별도로 확인합니다.",
      kind: "warn" as const,
      planActive: true,
    };
  }
  if (plan.plan_status === "canceled") {
    return {
      label: "조건부 진입 취소",
      detail: watcherSummary ?? "대기 진입 계획이 취소되었습니다.",
      kind: "neutral" as const,
      planActive: false,
    };
  }
  if (plan.plan_status === "expired") {
    return {
      label: "조건부 진입 만료",
      detail: "조건 충족 전 대기 진입 계획이 만료되었습니다.",
      kind: "neutral" as const,
      planActive: false,
    };
  }
  return {
    label: "조건부 진입 상태 확인",
    detail: "대기 진입 계획 상태를 확인할 수 없습니다.",
    kind: "neutral" as const,
    planActive: true,
  };
}

function formatPlanSide(value: string | null | undefined) {
  if (value === "long") {
    return "롱";
  }
  if (value === "short") {
    return "숏";
  }
  return "방향 미확인";
}

function entryPlanWatcherReasonCodes(plan: Row | null | undefined) {
  return dedupeNonEmptyReasons([
    ...asStringArray(plan?.watcher_reason_codes),
    ...asStringArray(plan?.blocked_reason_codes),
    rowString(plan, "plan_cancel_reason"),
    rowString(plan, "canceled_reason"),
  ].filter((value): value is string => typeof value === "string"));
}

function entryPlanConfirmationFailureSummary(plan: Row | null | undefined) {
  const failedReason = rowString(plan, "confirmation_failed_reason");
  const qualityReason = rowString(plan, "confirmation_quality_reason");
  const qualityState = rowString(plan, "confirmation_quality_state");
  const qualityScore = rowNumber(plan, "confirmation_quality_score");
  const qualityThreshold = rowNumber(plan, "confirmation_quality_threshold");
  const zoneEntered = rowBoolean(plan, "zone_touched") ?? rowBoolean(plan, "zone_entered");
  const parts: string[] = [];
  if (failedReason) {
    parts.push(`최근 확인 실패: ${formatInternalCodeLabel(failedReason)}`);
  }
  if (qualityReason && qualityReason !== failedReason) {
    parts.push(`품질 사유: ${formatInternalCodeLabel(qualityReason)}`);
  } else if (qualityState) {
    parts.push(`품질 상태: ${formatInternalCodeLabel(qualityState)}`);
  }
  if (zoneEntered === false || qualityReason === "ZONE_NOT_ENTERED" || failedReason === "ZONE_NOT_ENTERED") {
    parts.push("품질 평가는 구간 도달 후 산정");
  } else if (qualityScore !== null || qualityThreshold !== null) {
    parts.push(`품질 ${formatNumber(qualityScore, 4)} / 기준 ${formatNumber(qualityThreshold, 4)}`);
  }
  return parts.join(" / ") || null;
}

function entryPlanWatcherSummary(plan: Row | null | undefined) {
  const reasonCodes = entryPlanWatcherReasonCodes(plan);
  const reasonText = reasonCodes.length > 0 ? `감시 사유: ${formatTranslatedCodeList(reasonCodes)}` : null;
  const failureText = entryPlanConfirmationFailureSummary(plan);
  return [reasonText, failureText].filter((value): value is string => Boolean(value)).join(" / ") || null;
}

function formatPriceValue(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  const absValue = Math.abs(value);
  const digits = absValue >= 1000 ? 1 : absValue >= 1 ? 2 : 4;
  return formatNumber(value, digits);
}

function formatUsdtValue(value: number | null | undefined, digits = 2) {
  return value === null || value === undefined ? "-" : `${formatNumber(value, digits)} USDT`;
}

function formatPositionSide(value: string | null | undefined) {
  const normalized = value?.trim().toLowerCase();
  if (normalized === "long") {
    return "롱";
  }
  if (normalized === "short") {
    return "숏";
  }
  return value || "-";
}

function formatDistancePercent(from: number | null, to: number | null) {
  if (from === null || to === null || from <= 0) {
    return "-";
  }
  return formatRatio(Math.abs((to - from) / from));
}

function formatPositionRiskReward(row: PositionRow) {
  if (row.entryPrice === null || row.stopLoss === null || row.takeProfit === null) {
    return {
      value: "-",
      hint: "진입가, 손절가, 목표가가 모두 있어야 계산됩니다.",
    };
  }
  const side = row.side?.trim().toLowerCase();
  const risk = side === "short" ? row.stopLoss - row.entryPrice : row.entryPrice - row.stopLoss;
  const reward = side === "short" ? row.entryPrice - row.takeProfit : row.takeProfit - row.entryPrice;
  if (risk <= 0 || reward <= 0) {
    return {
      value: "구조 확인",
      hint: "진입 방향 대비 손절/목표 가격 구조가 일반적인 보호 구조와 다릅니다.",
    };
  }
  return {
    value: `${formatNumber(reward / risk, 2)}R`,
    hint: `진입 기준 손실폭 ${formatPriceValue(risk)} / 목표폭 ${formatPriceValue(reward)}`,
  };
}

function asPositionRow(row: Row): PositionRow {
  const rawOrderIds = Array.isArray(row.order_ids) ? row.order_ids : [];
  return {
    id: rowNumber(row, "id"),
    symbol: rowString(row, "symbol") ?? "UNKNOWN",
    mode: rowString(row, "mode"),
    side: rowString(row, "side"),
    status: rowString(row, "status"),
    quantity: rowNumber(row, "quantity") ?? rowNumber(row, "position_size"),
    entryPrice: rowNumber(row, "entry_price"),
    markPrice: rowNumber(row, "mark_price"),
    leverage: rowNumber(row, "leverage"),
    stopLoss: rowNumber(row, "stop_loss"),
    takeProfit: rowNumber(row, "take_profit"),
    realizedPnl: rowNumber(row, "realized_pnl"),
    unrealizedPnl: rowNumber(row, "unrealized_pnl"),
    openedAt: rowString(row, "opened_at") ?? rowString(row, "created_at"),
    updatedAt: rowString(row, "updated_at"),
    protected: rowBoolean(row, "protected"),
    protectiveOrderCount: rowNumber(row, "protective_order_count"),
    hasStopLoss: rowBoolean(row, "has_stop_loss"),
    hasTakeProfit: rowBoolean(row, "has_take_profit"),
    missingComponents: asStringArray(row.missing_components),
    orderIds: rawOrderIds.map((item) => String(item)).filter((item) => item.trim().length > 0),
  };
}

function positionProtectionTone(row: PositionRow) {
  if (row.protected && row.hasStopLoss && row.hasTakeProfit) {
    return "good" as const;
  }
  if (row.protected || row.hasStopLoss || row.hasTakeProfit) {
    return "warn" as const;
  }
  return "danger" as const;
}

function positionPnlClass(value: number | null) {
  if (value === null || value === 0) {
    return "text-slate-950";
  }
  return value > 0 ? "text-emerald-700" : "text-rose-700";
}

const orderLifecycleTabs: Array<{ key: OrderLifecycleTab; label: string; description: string }> = [
  {
    key: "summary",
    label: "요약",
    description: "포지션 단위 주문, 보호 주문, 체결 요약",
  },
  {
    key: "orders",
    label: "주문",
    description: "진입, 손절, 익절 주문 상태",
  },
  {
    key: "executions",
    label: "체결",
    description: "실제 execution row와 수수료, 실현손익",
  },
];

function historyTimestampMs(value: string | null | undefined) {
  if (!value) {
    return 0;
  }
  const parsed = new Date(value.endsWith("Z") ? value : `${value}Z`).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function latestHistoryTimestamp(values: Array<string | null>) {
  const latest = values.reduce((max, value) => Math.max(max, historyTimestampMs(value)), 0);
  return latest > 0 ? new Date(latest).toISOString() : null;
}

function earliestHistoryTimestamp(values: Array<string | null>) {
  const timestamps = values.map(historyTimestampMs).filter((value) => value > 0);
  if (timestamps.length === 0) {
    return null;
  }
  return new Date(Math.min(...timestamps)).toISOString();
}

function historyGroupKey(positionId: number | null, symbol: string, fallbackId: number | null, source: string) {
  if (positionId !== null) {
    return `position:${positionId}`;
  }
  if (fallbackId !== null) {
    return `${source}:${symbol}:${fallbackId}`;
  }
  return `unlinked:${symbol}`;
}

function asOrderHistoryRow(row: Row): OrderHistoryRow {
  return {
    id: rowNumber(row, "id"),
    symbol: rowString(row, "symbol") ?? "UNKNOWN",
    positionId: rowNumber(row, "position_id"),
    parentOrderId: rowNumber(row, "parent_order_id"),
    decisionRunId: rowNumber(row, "decision_run_id"),
    riskCheckId: rowNumber(row, "risk_check_id"),
    side: rowString(row, "side"),
    orderType: rowString(row, "order_type"),
    status: rowString(row, "status"),
    exchangeStatus: rowString(row, "exchange_status"),
    mode: rowString(row, "mode"),
    reduceOnly: rowBoolean(row, "reduce_only"),
    closeOnly: rowBoolean(row, "close_only"),
    requestedQuantity: rowNumber(row, "requested_quantity"),
    requestedPrice: rowNumber(row, "requested_price"),
    filledQuantity: rowNumber(row, "filled_quantity"),
    averageFillPrice: rowNumber(row, "average_fill_price"),
    externalOrderId: rowString(row, "external_order_id"),
    clientOrderId: rowString(row, "client_order_id"),
    reasonCodes: asStringArray(row.reason_codes),
    pnlSource: rowString(row, "pnl_source"),
    closeExecutionSyncStatus: rowString(row, "close_execution_sync_status"),
    realizedPnlConfirmed: rowBoolean(row, "realized_pnl_confirmed"),
    missingCloseExecution: rowBoolean(row, "missing_close_execution"),
    feeSource: rowString(row, "fee_source"),
    feeConfirmed: rowBoolean(row, "fee_confirmed"),
    warningMessage: rowString(row, "warning_message") ?? rowString(row, "blocked_reason"),
    feeWarningMessage: rowString(row, "fee_warning_message"),
    createdAt: rowString(row, "created_at"),
    updatedAt: rowString(row, "updated_at"),
    lastExchangeUpdateAt: rowString(row, "last_exchange_update_at"),
  };
}

function asExecutionHistoryRow(row: Row, ordersById: Map<number, OrderHistoryRow>): ExecutionHistoryRow {
  const orderId = rowNumber(row, "order_id");
  const matchedOrder = orderId !== null ? ordersById.get(orderId) : null;
  return {
    id: rowNumber(row, "id"),
    orderId,
    positionId: rowNumber(row, "position_id") ?? matchedOrder?.positionId ?? null,
    symbol: rowString(row, "symbol") ?? matchedOrder?.symbol ?? "UNKNOWN",
    status: rowString(row, "status"),
    orderType: rowString(row, "order_type") ?? matchedOrder?.orderType ?? null,
    orderStatus: rowString(row, "order_status") ?? matchedOrder?.status ?? null,
    fillPrice: rowNumber(row, "fill_price"),
    fillQuantity: rowNumber(row, "fill_quantity"),
    feePaid: rowNumber(row, "fee_paid"),
    realizedPnl: rowNumber(row, "realized_pnl"),
    externalTradeId: rowString(row, "external_trade_id"),
    commissionAsset: rowString(row, "commission_asset"),
    reduceOnly: matchedOrder?.reduceOnly ?? null,
    closeOnly: matchedOrder?.closeOnly ?? null,
    decisionRunId: rowNumber(row, "decision_run_id") ?? matchedOrder?.decisionRunId ?? null,
    createdAt: rowString(row, "created_at"),
    updatedAt: rowString(row, "updated_at"),
  };
}

function buildPositionHistoryGroups(orderRows: Row[], executionRows: Row[]) {
  const orders = orderRows.map(asOrderHistoryRow);
  const ordersById = new Map<number, OrderHistoryRow>();
  orders.forEach((order) => {
    if (order.id !== null) {
      ordersById.set(order.id, order);
    }
  });
  const executions = executionRows.map((row) => asExecutionHistoryRow(row, ordersById));
  const groups = new Map<string, PositionHistoryGroup>();

  const ensureGroup = (key: string, symbol: string, positionId: number | null) => {
    const existing = groups.get(key);
    if (existing) {
      return existing;
    }
    const group: PositionHistoryGroup = {
      key,
      positionId,
      symbol,
      orders: [],
      executions: [],
      createdAt: null,
      updatedAt: null,
    };
    groups.set(key, group);
    return group;
  };

  orders.forEach((order) => {
    const key = historyGroupKey(order.positionId, order.symbol, order.parentOrderId ?? order.id, "order");
    ensureGroup(key, order.symbol, order.positionId).orders.push(order);
  });

  executions.forEach((execution) => {
    const matchedOrder = execution.orderId !== null ? ordersById.get(execution.orderId) : null;
    const key = historyGroupKey(
      execution.positionId,
      execution.symbol,
      matchedOrder?.parentOrderId ?? execution.orderId ?? execution.id,
      "order",
    );
    ensureGroup(key, execution.symbol, execution.positionId).executions.push(execution);
  });

  const result = [...groups.values()].map((group) => {
    group.orders.sort((left, right) => historyTimestampMs(left.createdAt) - historyTimestampMs(right.createdAt));
    group.executions.sort((left, right) => historyTimestampMs(left.createdAt) - historyTimestampMs(right.createdAt));
    group.createdAt = earliestHistoryTimestamp([
      ...group.orders.map((order) => order.createdAt),
      ...group.executions.map((execution) => execution.createdAt),
    ]);
    group.updatedAt = latestHistoryTimestamp([
      ...group.orders.map((order) => order.updatedAt ?? order.createdAt),
      ...group.executions.map((execution) => execution.updatedAt ?? execution.createdAt),
    ]);
    return group;
  });

  return result.sort((left, right) => historyTimestampMs(right.updatedAt) - historyTimestampMs(left.updatedAt));
}

function sumHistory(values: Array<number | null>) {
  return values.reduce<number>((total, value) => total + (value ?? 0), 0);
}

function orderSettlementInputs(orders: OrderHistoryRow[]): OrderSettlementInput[] {
  return orders.map((order) => ({
    closeExecutionSyncStatus: order.closeExecutionSyncStatus,
    missingCloseExecution: order.missingCloseExecution,
    realizedPnlConfirmed: order.realizedPnlConfirmed,
    pnlSource: order.pnlSource,
    feeSource: order.feeSource,
    feeConfirmed: order.feeConfirmed,
    warningMessage: order.warningMessage,
    feeWarningMessage: order.feeWarningMessage,
  }));
}

function orderIntentLabel(order: OrderHistoryRow) {
  const type = order.orderType?.toLowerCase() ?? "";
  if (order.reduceOnly || order.closeOnly) {
    if (type.includes("take_profit")) {
      return "익절 보호";
    }
    if (type.includes("stop")) {
      return "손절 보호";
    }
    return "청산/감소";
  }
  return "진입";
}

function isCloseExecutionHistory(execution: ExecutionHistoryRow) {
  const type = execution.orderType?.toLowerCase() ?? "";
  return Boolean(execution.reduceOnly || execution.closeOnly || type.includes("stop") || type.includes("take_profit"));
}

function orderStatusTone(status: string | null | undefined) {
  const normalized = status?.trim().toLowerCase();
  if (normalized === "filled") {
    return "good" as const;
  }
  if (normalized === "rejected") {
    return "danger" as const;
  }
  if (normalized === "pending" || normalized === "partially_filled" || normalized === "new") {
    return "warn" as const;
  }
  return "neutral" as const;
}

function positionHistoryStatus(group: PositionHistoryGroup) {
  const settlement = summarizeSettlementDisplay(orderSettlementInputs(group.orders));
  if (settlement.realizedPnlDisplayMode === "pending") {
    return {
      label: settlement.badgeLabel ?? "정산 동기화 대기",
      detail: settlement.warningMessage ?? settlement.realizedPnlDetail,
      kind: settlement.badgeTone === "danger" ? ("danger" as const) : ("warn" as const),
    };
  }
  const reasonCodes = group.orders.flatMap((order) => order.reasonCodes);
  if (group.orders.some((order) => order.status?.toLowerCase() === "rejected")) {
    return {
      label: "주문 거절 있음",
      detail: "같은 포지션 묶음 안에 거래소 제출 실패 또는 거절 주문이 있습니다.",
      kind: "danger" as const,
    };
  }
  if (reasonCodes.includes("POSITION_CLOSED_PROTECTIVE_ORDER_ORPHANED")) {
    return {
      label: "보호 주문 정리됨",
      detail: "포지션 종료 후 거래소 오픈 주문 목록에서 사라진 보호 주문을 로컬에서 정리했습니다. 실제 청산 체결 누락 여부는 체결 탭에서 확인합니다.",
      kind: "warn" as const,
    };
  }
  if (group.orders.some((order) => order.status?.toLowerCase() === "pending")) {
    return {
      label: "미종결 주문 있음",
      detail: "아직 pending 상태인 주문이 있습니다.",
      kind: "warn" as const,
    };
  }
  if (group.executions.length > 0) {
    return {
      label: "체결 기록 있음",
      detail: "execution row가 연결되어 있어 실제 체결 수량과 수수료를 확인할 수 있습니다.",
      kind: "good" as const,
    };
  }
  return {
    label: "주문 기록만 있음",
    detail: "이 묶음에는 주문 row만 있고 execution row는 없습니다.",
    kind: "neutral" as const,
  };
}

function OrderLifecycleTabLinks({
  activeTab,
  selectedSymbol,
  selectedPositionId,
}: {
  activeTab: OrderLifecycleTab;
  selectedSymbol?: string | null;
  selectedPositionId?: number | null;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {orderLifecycleTabs.map((tab) => {
        const active = tab.key === activeTab;
        const href = ordersViewHref({
          tab: tab.key,
          symbol: selectedSymbol,
          positionId: selectedPositionId,
        });
        return (
          <Link
            key={tab.key}
            href={href}
            className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${
              active
                ? "border-blue-600 bg-blue-600 text-white"
                : "border-slate-200 bg-white text-slate-700 hover:border-blue-200 hover:bg-blue-50"
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </div>
  );
}

function PositionHistorySummaryPanel({ group }: { group: PositionHistoryGroup }) {
  const entryOrders = group.orders.filter((order) => !order.reduceOnly && !order.closeOnly);
  const protectiveOrders = group.orders.filter((order) => order.reduceOnly || order.closeOnly);
  const filledQuantity = sumHistory(group.executions.map((execution) => execution.fillQuantity));
  const feeTotal = sumHistory(group.executions.map((execution) => execution.feePaid));
  const realizedPnl = sumHistory(group.executions.map((execution) => execution.realizedPnl));
  const settlement = summarizeSettlementDisplay(orderSettlementInputs(group.orders));
  const realizedPnlLabel =
    settlement.realizedPnlDisplayMode === "pending"
      ? settlement.realizedPnlLabel ?? "동기화 대기"
      : formatUsdtValue(realizedPnl, 4);
  const reasonCodes = [...new Set(group.orders.flatMap((order) => order.reasonCodes))];
  const latestExecution = group.executions.at(-1);
  const latestOrder = group.orders.at(-1);

  return (
    <div className="grid gap-3 lg:grid-cols-3">
      {metricCard("주문 구성", `진입 ${entryOrders.length} / 보호 ${protectiveOrders.length}`, "같은 position_id 기준")}
      {metricCard("체결 합계", formatNumber(filledQuantity, 6), `${group.executions.length}개 execution row 기준`)}
      {metricCard(
        "수수료 / 실현손익",
        `${formatUsdtValue(feeTotal, 4)} / ${realizedPnlLabel}`,
        `${settlement.feeDetail} / 손익: ${settlement.realizedPnlDetail}`,
      )}
      {settlement.badgeLabel
        ? metricCard("정산/체결 동기화", settlement.badgeLabel, settlement.warningMessage ?? settlement.realizedPnlDetail, {
            compact: true,
          })
        : null}
      {metricCard(
        "최근 주문",
        latestOrder ? `#${latestOrder.id ?? "-"} ${formatDisplayValue(latestOrder.status ?? "-", "status")}` : "-",
        latestOrder ? `${orderIntentLabel(latestOrder)} / ${formatDateTime(latestOrder.updatedAt ?? latestOrder.createdAt)}` : "주문 없음",
        { compact: true },
      )}
      {metricCard(
        "최근 체결",
        latestExecution ? `#${latestExecution.id ?? "-"} ${formatPriceValue(latestExecution.fillPrice)}` : "-",
        latestExecution
          ? `${formatNumber(latestExecution.fillQuantity, 6)} / ${formatDateTime(latestExecution.createdAt)}`
          : "체결 없음",
        { compact: true },
      )}
      {metricCard("정리 코드", formatTranslatedCodeList(reasonCodes), "비어 있으면 별도 reason code 없음", { compact: true })}
    </div>
  );
}

function PositionHistoryOrdersPanel({ group }: { group: PositionHistoryGroup }) {
  if (group.orders.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-slate-300 bg-white px-4 py-6 text-sm text-slate-500">
        이 포지션 묶음에는 주문 row가 없습니다.
      </div>
    );
  }

  return (
    <div className="divide-y divide-slate-200 rounded-md border border-slate-200 bg-white">
      {group.orders.map((order, index) => (
        <div key={order.id ?? `${group.key}-order-${index}`} className="px-4 py-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-slate-950">
                #{order.id ?? "-"} {orderIntentLabel(order)} / {formatDisplayValue(order.orderType ?? "-", "order_type")}
              </p>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                생성 {formatDateTime(order.createdAt)} / 갱신 {formatDateTime(order.updatedAt)}
              </p>
            </div>
            <span className={`w-fit rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(orderStatusTone(order.status))}`}>
              {formatDisplayValue(order.status ?? "-", "status")}
            </span>
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {metricCard(
              "수량 / 체결",
              `${formatNumber(order.requestedQuantity, 6)} / ${formatNumber(order.filledQuantity, 6)}`,
              `평균 체결가 ${formatPriceValue(order.averageFillPrice)}`,
              { compact: true },
            )}
            {metricCard("요청 가격", formatPriceValue(order.requestedPrice), `방향 ${order.side ?? "-"}`, { compact: true })}
            {metricCard(
              "거래소 상태",
              formatDisplayValue(order.exchangeStatus ?? "-", "exchange_status"),
              `거래소 ID ${order.externalOrderId ?? "-"}`,
              { compact: true },
            )}
            {metricCard(
              "연결 ID",
              `decision #${order.decisionRunId ?? "-"}`,
              `risk #${order.riskCheckId ?? "-"} / parent #${order.parentOrderId ?? "-"}`,
              { compact: true },
            )}
          </div>
          {order.reasonCodes.length > 0 ? (
            <p className="mt-3 break-words rounded-md bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">
              reason: {formatTranslatedCodeList(order.reasonCodes)}
            </p>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function PositionHistoryExecutionsPanel({ group }: { group: PositionHistoryGroup }) {
  const settlement = summarizeSettlementDisplay(orderSettlementInputs(group.orders));
  if (group.executions.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-slate-300 bg-white px-4 py-6 text-sm text-slate-500">
        {settlement.realizedPnlDisplayMode === "pending"
          ? "청산 execution row가 없어 거래소 손익이 아직 반영되지 않았습니다."
          : "이 포지션 묶음에는 execution row가 없습니다."}
      </div>
    );
  }

  return (
    <div className="rounded-md border border-slate-200 bg-white">
      {settlement.realizedPnlDisplayMode === "pending" ? (
        <div className="border-b border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
          {settlement.warningMessage ?? "청산 체결 누락"} / {settlement.feeDetail}
        </div>
      ) : null}
      <div className="divide-y divide-slate-200">
      {group.executions.map((execution, index) => {
        const entryOnlyWhileCloseMissing =
          settlement.realizedPnlDisplayMode === "pending" && !isCloseExecutionHistory(execution);
        return (
        <div key={execution.id ?? `${group.key}-execution-${index}`} className="px-4 py-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-slate-950">
                execution #{execution.id ?? "-"} / 주문 #{execution.orderId ?? "-"}
              </p>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                체결 기록 {formatDateTime(execution.createdAt)} / trade {execution.externalTradeId ?? "-"}
              </p>
            </div>
            <span className={`w-fit rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(orderStatusTone(execution.status))}`}>
              {formatDisplayValue(execution.status ?? "-", "status")}
            </span>
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {metricCard(
              "체결가 / 수량",
              `${formatPriceValue(execution.fillPrice)} / ${formatNumber(execution.fillQuantity, 6)}`,
              formatDisplayValue(execution.orderType ?? "-", "order_type"),
              { compact: true },
            )}
                {metricCard(
              "수수료",
              formatUsdtValue(execution.feePaid, 4),
              entryOnlyWhileCloseMissing
                ? `자산 ${execution.commissionAsset ?? "-"} / 청산 수수료 미반영`
                : execution.commissionAsset ? `자산 ${execution.commissionAsset}` : "수수료 자산 미확인",
              { compact: true },
            )}
            {metricCard(
              "실현손익",
              entryOnlyWhileCloseMissing ? "포지션 손익 미반영" : formatUsdtValue(execution.realizedPnl, 4),
              entryOnlyWhileCloseMissing ? "진입 execution row 기준 0은 최종 손익 아님" : "execution.realized_pnl 기준",
              { compact: true },
            )}
            {metricCard(
              "연결 상태",
              execution.positionId !== null ? `position #${execution.positionId}` : "position 미연결",
              `decision #${execution.decisionRunId ?? "-"} / 주문 상태 ${formatDisplayValue(execution.orderStatus ?? "-", "status")}`,
              { compact: true },
            )}
          </div>
        </div>
        );
      })}
      </div>
    </div>
  );
}

function formatPlanDistance(currentPrice: number | null, zoneMin: number | null, zoneMax: number | null) {
  if (currentPrice === null || zoneMin === null || zoneMax === null || currentPrice <= 0) {
    return "현재가 또는 진입 구간 정보가 부족합니다.";
  }
  if (currentPrice >= zoneMin && currentPrice <= zoneMax) {
    return "현재가가 대기 구간 안에 있습니다. 도달 후에도 즉시 주문이 아니라 재판단을 거칩니다.";
  }
  const target = currentPrice < zoneMin ? zoneMin : zoneMax;
  const distancePct = ((target - currentPrice) / currentPrice) * 100;
  const direction = distancePct > 0 ? "위" : "아래";
  const signed = `${distancePct >= 0 ? "+" : ""}${distancePct.toFixed(2)}%`;
  return `대기 구간은 현재가보다 ${signed} ${direction}에 있습니다.`;
}

function entryPlanConfirmationPresentation(
  triggerDetails: Record<string, unknown> | null,
  sideLabel: string,
) {
  if (!triggerDetails || rowBoolean(triggerDetails, "confirm_met") !== false) {
    return null;
  }
  const zoneEntered = rowBoolean(triggerDetails, "zone_entered");
  const reason = rowString(triggerDetails, "reason");
  const scoreText = entryPlanQualityScoreText(triggerDetails);
  const zoneText =
    zoneEntered === true
      ? "대기 구간에는 들어왔지만"
      : zoneEntered === false
        ? "아직 대기 구간 밖이고"
        : "구간 도달 여부를 확인 중이고";
  const reasonText = reason ? ` 원본 사유: ${formatInternalCodeLabel(reason)}.` : "";

  return {
    label: `${sideLabel} 대기 계획 / 확인 조건 미충족`,
    detail: `${zoneText} ${scoreText} 상태라 주문 단계로 넘기지 않았습니다.${reasonText}`,
    actionTitle: "확인 조건 대기",
    actionDetail: "구간 도달과 확인 조건이 모두 충족되면 AI 재판단 → 리스크 승인 → 주문 실행 순서로 진행합니다.",
  };
}

function entryPlanQualityScoreText(triggerDetails: Record<string, unknown> | null) {
  const zoneEntered = rowBoolean(triggerDetails, "zone_entered");
  const reason = rowString(triggerDetails, "reason");
  if (zoneEntered === false || reason === "ZONE_NOT_ENTERED") {
    return "구간 도달 후 산정";
  }
  const qualityScore = asFiniteNumber(triggerDetails?.quality_score);
  const qualityThreshold = asFiniteNumber(triggerDetails?.quality_threshold);
  if (qualityScore !== null && qualityThreshold !== null) {
    return `확인 점수 ${formatNumber(qualityScore, 2)} / 기준 ${formatNumber(qualityThreshold, 2)}`;
  }
  return "확인 점수 미충족";
}

function emptyEntryPlanDetails() {
  return {
    planId: null as number | null,
    active: false,
    status: null as string | null,
    label: "대기 중인 진입 플랜 없음",
    detail: "현재 저장된 진입 대기 구간이 없습니다.",
    zoneText: "지정 구간 없음",
    distanceText: "구간 도달 감시 대상이 아닙니다.",
    actionTitle: "자동 주문 없음",
    actionDetail: "새 판단에서 플랜이 생성되기 전까지 감시할 가격 구간이 없습니다.",
    invalidationText: "-",
    expiresText: "-",
    kind: "neutral" as const,
  };
}

function entryPlanDetailsFromRecord(
  plan: Record<string, unknown> | null,
  currentPrice: number | null | undefined,
  options?: { decisionRunId?: number | null; requireSourceMatch?: boolean },
) {
  const planId = rowNumber(plan, "plan_id");
  if (planId === null) {
    return emptyEntryPlanDetails();
  }

  const sourceDecisionRunId = rowNumber(plan, "source_decision_run_id");
  if (
    options?.requireSourceMatch &&
    sourceDecisionRunId !== null &&
    options.decisionRunId !== null &&
    options.decisionRunId !== sourceDecisionRunId
  ) {
    return emptyEntryPlanDetails();
  }

  const side = rowString(plan, "side");
  const sideLabel = formatPlanSide(side);
  const status = rowString(plan, "plan_status") ?? "armed";
  const zoneMin = rowNumber(plan, "entry_zone_min");
  const zoneMax = rowNumber(plan, "entry_zone_max");
  const invalidation = rowNumber(plan, "invalidation_price");
  const expiresAt = rowString(plan, "expires_at");
  const zoneText =
    zoneMin !== null && zoneMax !== null
      ? `${formatPriceValue(zoneMin)} ~ ${formatPriceValue(zoneMax)}`
      : "진입 구간 미확인";
  const statusLabel =
    status === "armed"
      ? `${sideLabel} 진입 플랜 대기 중`
      : status === "canceled"
        ? "진입 플랜 취소됨"
        : `플랜 상태 ${formatInternalCodeLabel(status)}`;
  const triggerDetails = asRecord(plan?.trigger_details);
  const confirmation = status === "armed" ? entryPlanConfirmationPresentation(triggerDetails, sideLabel) : null;
  const watcherSummary = entryPlanWatcherSummary(plan);
  const canceledDetail = watcherSummary ?? "이전 진입 플랜이 취소되었습니다.";
  const detail =
    status === "canceled"
      ? canceledDetail
      : confirmation?.detail ?? "지정 구간에 도달하면 즉시 주문하지 않고 AI 재판단과 리스크 승인을 다시 거칩니다.";
  const actionTitle = status === "canceled" ? "감시 종료" : confirmation?.actionTitle ?? "도달 시 재판단";
  const actionDetail =
    status === "canceled"
      ? "취소된 플랜은 더 이상 감시하지 않습니다."
      : confirmation?.actionDetail ?? "구간 도달 → AI 재판단 → 리스크 승인 → 승인된 주문만 실행";

  return {
    planId,
    active: status === "armed" || status === "triggered",
    status,
    label: confirmation?.label ?? statusLabel,
    detail,
    zoneText,
    distanceText: formatPlanDistance(currentPrice ?? null, zoneMin, zoneMax),
    actionTitle,
    actionDetail,
    invalidationText: invalidation !== null ? formatPriceValue(invalidation) : "-",
    expiresText: formatDateTime(expiresAt),
    kind: status === "canceled" || status === "expired" ? ("neutral" as const) : ("warn" as const),
  };
}

function activeEntryPlanDetails(
  symbol: OperatorSymbol | null | undefined,
  options?: { decisionRunId?: number | null; requireSourceMatch?: boolean },
) {
  return entryPlanDetailsFromRecord(asRecord(symbol?.pending_entry_plan), symbol?.latest_price, options);
}

function riskEntryPlanDetails(row: RiskCheckRow, symbol: OperatorSymbol | null | undefined) {
  const linkedPlan = entryPlanDetailsFromRecord(asRecord(row.pending_entry_plan), symbol?.latest_price, {
    decisionRunId: row.decision_run_id ?? null,
    requireSourceMatch: true,
  });
  if (linkedPlan.planId !== null) {
    return linkedPlan;
  }
  return activeEntryPlanDetails(symbol, {
    decisionRunId: row.decision_run_id ?? null,
    requireSourceMatch: true,
  });
}

function riskRowCreatedAtMs(row: RiskCheckRow) {
  if (!row.created_at) {
    return 0;
  }
  const parsed = Date.parse(row.created_at.endsWith("Z") ? row.created_at : `${row.created_at}Z`);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function riskDisplayGroupKey(row: RiskCheckRow, index: number) {
  if (row.decision_run_id !== null && row.decision_run_id !== undefined) {
    return `${row.symbol ?? "unknown"}|decision:${row.decision_run_id}`;
  }
  return row.id !== null && row.id !== undefined
    ? `risk:${row.id}`
    : `risk:${row.symbol ?? "unknown"}:${row.created_at ?? index}`;
}

function riskDisplayScore(row: RiskCheckRow, symbol: OperatorSymbol | null | undefined) {
  const plan = asRecord(row.pending_entry_plan) ?? asRecord(symbol?.pending_entry_plan);
  const sourceDecisionRunId = rowNumber(plan, "source_decision_run_id");
  const decision = row.decision?.trim().toLowerCase();
  const nonHoldDecision = Boolean(decision && decision !== "hold");
  let score = riskRowCreatedAtMs(row) / 1_000_000_000;
  if (sourceDecisionRunId !== null && row.decision_run_id === sourceDecisionRunId && nonHoldDecision) {
    score += 100;
  }
  if (nonHoldDecision) {
    score += 40;
  }
  if (row.allowed === true) {
    score += 20;
  }
  if (!hasOnlyPassiveEntryReasons(row.reason_codes)) {
    score += 10;
  }
  return score;
}

function operatorSymbolMap(operator?: OperatorDashboardPayload | null) {
  const symbols = new Map<string, OperatorSymbol>();
  for (const symbol of operator?.symbols ?? []) {
    symbols.set(symbol.symbol, symbol);
  }
  return symbols;
}

function riskDisplayRows(rows: RiskCheckRow[], symbolsByName?: Map<string, OperatorSymbol>) {
  const groups = new Map<string, { row: RiskCheckRow; score: number; order: number }>();
  rows.forEach((row, index) => {
    const symbol = symbolsByName?.get(row.symbol ?? "") ?? null;
    const key = riskDisplayGroupKey(row, index);
    const score = riskDisplayScore(row, symbol);
    const existing = groups.get(key);
    if (!existing || score > existing.score) {
      groups.set(key, { row, score, order: existing?.order ?? index });
    }
  });
  return [...groups.values()].sort((left, right) => left.order - right.order).map((item) => item.row);
}

function aiSkipReasonCopy(value: string | null | undefined) {
  const copy = describeAiSkipReason(value);
  return {
    label: copy.title_ko,
    detail: copy.detail_ko,
    nextStep: copy.next_step_ko,
  };
}

function isMeaningfulAiSkipReason(value: string | null | undefined) {
  const normalized = value?.trim().toUpperCase();
  return Boolean(normalized && normalized !== "NO_EVENT" && normalized !== "TRIGGER_DEDUPED");
}

function statusPanelClass(kind: "good" | "warn" | "danger" | "neutral") {
  return {
    good: "border-emerald-200 bg-emerald-50 text-emerald-950",
    warn: "border-amber-200 bg-amber-50 text-amber-950",
    danger: "border-rose-200 bg-rose-50 text-rose-950",
    neutral: "border-slate-200 bg-slate-50 text-slate-900",
  }[kind];
}

function riskRowStatusPresentation(
  row: RiskCheckRow,
  plan: ReturnType<typeof activeEntryPlanDetails>,
) {
  const skipReason = riskSkipReason(row);
  const skipCopy = aiSkipReasonCopy(skipReason);
  if (plan.active) {
    return {
      label: plan.label,
      detail: plan.detail,
      nextStep: plan.actionDetail,
      kind: plan.kind,
    };
  }
  if (plan.status === "canceled" || plan.status === "expired") {
    return {
      label: plan.label,
      detail: plan.detail,
      nextStep: plan.actionDetail,
      kind: plan.kind,
    };
  }
  if (isMeaningfulAiSkipReason(skipReason)) {
    return {
      label: "AI 검토 생략",
      detail: skipCopy.detail,
      nextStep: skipCopy.nextStep,
      kind: "warn" as const,
    };
  }
  if (row.allowed === true) {
    return {
      label: "리스크 승인됨",
      detail: "AI 판단 또는 규칙 기반 판단이 리스크 점검을 통과했습니다.",
      nextStep: "실제 주문 제출 여부는 실행 상태에서 확인하세요.",
      kind: "good" as const,
    };
  }
  if (hasOnlyPassiveEntryReasons(row.reason_codes)) {
    return {
      label: "현재 대기 중인 진입 플랜 없음",
      detail: "마지막 판단은 HOLD이며, 저장된 가격 대기 구간도 없습니다.",
      nextStep: "새 후보가 생기거나 AI가 감시용 진입 계획을 제출하면 플랜 대기 상태로 바뀝니다.",
      kind: "neutral" as const,
    };
  }
  if (isAiHoldBaselineDisagreement(row)) {
    return {
      label: "AI HOLD로 즉시 주문 보류",
      detail: "엔진 기준선은 진입 후보였지만 AI 최종 판단이 HOLD라 바로 주문하지 않았습니다.",
      nextStep: "대기 플랜이 있으면 구간 도달 후 AI 재판단과 리스크 승인을 다시 거칩니다. 최신 플랜 상태를 확인하세요.",
      kind: "warn" as const,
    };
  }
  if (row.allowed === false) {
    return {
      label: "리스크 차단",
      detail: formatTranslatedCodeList(row.reason_codes),
      nextStep: "차단 사유가 해소되기 전에는 주문이 제출되지 않습니다.",
      kind: "danger" as const,
    };
  }
  return {
    label: "상태 확인 필요",
    detail: "이 row만으로 현재 플랜 또는 실행 상태를 확정할 수 없습니다.",
    nextStep: "고급 정보와 연결된 decision row를 확인하세요.",
    kind: "neutral" as const,
  };
}

function riskDecisionPresentation(
  row: RiskCheckRow,
  plan: ReturnType<typeof activeEntryPlanDetails>,
) {
  if (plan.status === "armed" && isEntryDecision(row.decision)) {
    return {
      label: `${formatPlanSide(row.decision)} 대기 계획`,
      hint: "실제 포지션 진입 완료가 아니라 대기 진입 계획의 방향입니다.",
    };
  }
  return {
    label: translateDecision(row.decision),
    hint: "리스크 대상 결정",
  };
}

function isAiHoldBaselineDisagreement(row: RiskCheckRow) {
  const decision = row.decision?.trim().toLowerCase();
  return (
    row.allowed === false &&
    (decision === "long" || decision === "short") &&
    (row.reason_codes ?? []).includes("DETERMINISTIC_BASELINE_DISAGREEMENT")
  );
}

function riskReasonEvidence(row: RiskCheckRow) {
  const payload = asRecord(row.payload);
  const evidence = asRecord(payload?.reason_evidence) ?? asRecord(payload?.debug_payload);
  const reasons = row.reason_codes ?? [];
  const parts: string[] = [];

  if (reasons.includes("SYMBOL_RECENT_PERFORMANCE_NEGATIVE")) {
    const gate = asRecord(evidence?.symbol_recent_performance_gate);
    const symbol = row.symbol ?? rowString(gate, "symbol") ?? "해당 심볼";
    const netPnl = rowNumber(gate, "net_pnl_after_fees");
    const grossPnl = rowNumber(gate, "gross_realized_pnl");
    const feeTotal = rowNumber(gate, "fee_total");
    const executionCount = rowNumber(gate, "execution_count");
    const lookbackDays = rowNumber(gate, "lookback_days");
    if (gate?.status === "blocked" || netPnl !== null) {
      parts.push(
        `${symbol} 최근 ${formatNumber(lookbackDays, 0)}일 ${formatNumber(
          executionCount,
          0,
        )}건 기준 순손익 ${formatNumber(netPnl, 4)} USDT (gross ${formatNumber(grossPnl, 4)}, fee ${formatNumber(
          feeTotal,
          4,
        )})`,
      );
    }
  }

  if (reasons.includes("DECISION_BUCKET_RECENT_PERFORMANCE_NEGATIVE")) {
    const gate = asRecord(evidence?.decision_bucket_recent_performance_gate);
    const bucket = rowString(gate, "bucket_key") ?? "same decision bucket";
    const netPnl = rowNumber(gate, "net_pnl_after_fees");
    const expectancy = rowNumber(gate, "expectancy_after_fees");
    const fillCount = rowNumber(gate, "fill_count");
    const lookbackDays = rowNumber(gate, "lookback_days");
    if (gate?.status === "blocked" || netPnl !== null) {
      parts.push(
        `${bucket} 최근 ${formatNumber(lookbackDays, 0)}일 ${formatNumber(
          fillCount,
          0,
        )}개 체결 기준 순손익 ${formatNumber(netPnl, 4)} USDT / 기대값 ${formatNumber(expectancy, 4)} USDT`,
      );
    }
  }

  if (reasons.includes("CORRELATED_EXPOSURE_LIMIT_REACHED")) {
    const gate = asRecord(evidence?.portfolio_exposure_gate);
    const limits = asRecord(gate?.limits);
    const combinedPct = rowNumber(gate, "combined_BTC_ETH_directional_exposure_pct");
    const correlatedPct = rowNumber(gate, "correlated_symbol_exposure_pct");
    const directionalPct = rowNumber(gate, "directional_bias_pct");
    const limitPct =
      rowNumber(limits, "max_same_direction_major_exposure_pct") ?? rowNumber(limits, "directional_bias_pct");
    const pct = combinedPct ?? correlatedPct ?? directionalPct;
    if (pct !== null || limitPct !== null) {
      parts.push(
        `BTC/ETH 동일방향 노출 ${formatNumber(pct, 2)}% / 한도 ${formatNumber(
          limitPct,
          2,
        )}%로 신규 진입 전 차단`,
      );
    }
  }

  const entryTrigger = asRecord(evidence?.entry_trigger);
  if (
    reasons.some((reason) => ["ENTRY_TRIGGER_NOT_MET", "CHASE_LIMIT_EXCEEDED", "SLIPPAGE_THRESHOLD_EXCEEDED"].includes(reason)) &&
    entryTrigger
  ) {
    const latestPrice = rowNumber(entryTrigger, "latest_price");
    const zoneMin = rowNumber(entryTrigger, "entry_zone_min");
    const zoneMax = rowNumber(entryTrigger, "entry_zone_max");
    const observedChase = rowNumber(entryTrigger, "observed_chase_bps");
    const maxChase = rowNumber(entryTrigger, "max_chase_bps");
    parts.push(
      `현재가 ${formatPriceValue(latestPrice)}, 진입 구간 ${formatPriceValue(zoneMin)}~${formatPriceValue(
        zoneMax,
      )}, 추격 ${formatNumber(observedChase, 2)}bps / 허용 ${formatNumber(maxChase, 2)}bps`,
    );
  }

  return parts.join(" / ");
}

function riskNoTradeReason(
  row: RiskCheckRow,
  plan: ReturnType<typeof activeEntryPlanDetails>,
  fallbackHint: string,
) {
  const skipReason = riskSkipReason(row);
  if (plan.active) {
    if (plan.status === "armed" && plan.label.includes("확인 조건 미충족")) {
      return {
        label: "확인 조건 미충족으로 주문 대기",
        hint: plan.detail,
      };
    }
    return {
      label: "플랜은 대기 중이며, 구간 도달 전에는 주문하지 않습니다.",
      hint: "가격이 대기 구간에 도달하면 AI 재판단과 리스크 승인을 다시 거칩니다.",
    };
  }
  if (plan.status === "canceled" || plan.status === "expired") {
    return {
      label: plan.label,
      hint: plan.detail,
    };
  }
  if (isMeaningfulAiSkipReason(skipReason)) {
    const skipCopy = aiSkipReasonCopy(skipReason);
    return {
      label: skipCopy.label,
      hint: skipCopy.detail,
    };
  }
  if (isAiHoldBaselineDisagreement(row)) {
    return {
      label: "AI 최종 판단이 HOLD라 즉시 주문하지 않았습니다.",
      hint: "추격 한도, 트리거, 슬리피지 등은 함께 저장된 내부 검증 코드입니다. 현재 row의 1차 보류 사유는 AI HOLD입니다.",
    };
  }
  if (hasOnlyPassiveEntryReasons(row.reason_codes)) {
    return {
      label: "현재는 신규 진입 신호가 없어 대기 중입니다.",
      hint: "새 후보가 생기거나 AI가 감시용 진입 계획을 제출하면 플랜 대기 상태로 바뀝니다.",
    };
  }
  const evidence = riskReasonEvidence(row);
  if (evidence) {
    return {
      label: formatTranslatedCodeList(row.reason_codes),
      hint: evidence,
    };
  }
  return {
    label: formatTranslatedCodeList(row.reason_codes),
    hint: fallbackHint,
  };
}

function protectionReviewPresentation(symbol: OperatorSymbol) {
  const protection = protectionReadModel(symbol);
  const reason = protection.blocked_reason_code ?? protection.blocked_reason ?? protection.last_error;
  const deterministicHint = "AI 판단과 별개로 규칙 기반 보호 주문 점검/복구 경로에서 확인합니다.";

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
      label: "보호 주문 복구 상태",
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
  const sharedCopy = describeReasonCode(value);
  if (sharedCopy.known && sharedCopy.raw_code) {
    return sharedCopy.title_ko;
  }
  const extraReasonCodeLabelMap: Record<string, string> = {
    ENTRY_AUTO_RESIZED: "진입 수량이 자동 축소 승인되었습니다.",
    ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT: "총 노출 한도에 맞게 진입 수량이 축소되었습니다.",
    ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT: "방향 편향 한도에 맞게 진입 수량이 축소되었습니다.",
    ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT: "최대 단일 포지션 한도에 맞게 진입 수량이 축소되었습니다.",
    ENTRY_CLAMPED_TO_SAME_TIER_LIMIT: "동일 티어 집중도 한도에 맞게 진입 수량이 축소되었습니다.",
    ENTRY_SIZE_BELOW_MIN_NOTIONAL: "최소 실행 가능 주문 미만",
    ENTRY_TRIGGER_NOT_MET: "진입 조건이 아직 충족되지 않았습니다",
    SYMBOL_RECENT_PERFORMANCE_NEGATIVE: "최근 해당 심볼 실현손익이 수수료 차감 후 음수입니다",
    CORRELATED_EXPOSURE_LIMIT_REACHED: "BTC/ETH 동일방향 상관 노출 한도를 초과했습니다",
    MACRO_EVENT_RESULT_CONFLICT: "발표 결과와 진입 방향 충돌",
    CHASE_LIMIT_EXCEEDED: "가격이 이미 지나가 추격 진입을 막았습니다",
    LOW_CONFIDENCE: "진입 확신도 부족",
    TRANSITION_FRAGILE: "전환 레짐이 불안정",
    TOP_TRADER_LONG_CROWDED: "상위 트레이더 롱 쏠림",
    LOW_EDGE_HOLD_CANDIDATE: "거래 우위가 약해 관망",
    BREADTH_WEAKNESS: "시장 폭 약화",
    LOW_CONFIDENCE_PULLBACK: "눌림목 진입 확신도 부족",
    DERIVATIVES_HEADWIND: "파생시장 역풍",
    INVALID_INVALIDATION_PRICE: "무효화 가격 기준 이상",
    ENGINE_TREND_PULLBACK_ENGINE: "눌림목 진입 엔진",
    ENGINE_TREND_CONTINUATION_ENGINE: "추세 지속 엔진 기준",
    ENGINE_RANGE_MEAN_REVERSION_ENGINE: "박스권 평균회귀 엔진",
    ENGINE_BREAKOUT_EXCEPTION_ENGINE: "돌파 예외 엔진",
    REGIME_BULLISH: "상승 레짐",
    REGIME_BEARISH: "하락 레짐",
    REGIME_RANGE: "횡보 레짐",
    REGIME_TRANSITION: "전환 레짐",
    TREND_BULLISH_ALIGNED: "상승 추세 정렬",
    TREND_BEARISH_ALIGNED: "하락 추세 정렬",
    TREND_RANGE: "횡보 추세",
    TREND_MIXED: "추세 혼재",
    EXPECTANCY_NEUTRAL: "기대값 중립",
    DERIVATIVES_NEUTRAL: "파생시장 중립",
    LEAD_MARKETS_ALIGNED: "선행 시장 정렬",
    LEAD_MARKETS_NEUTRAL: "선행 시장 중립",
    LEAD_MARKET_CONFIDENCE_DISCOUNT: "선행 시장 불일치로 신뢰도 축소",
    AI_WATCH_ENTRY_PLAN: "진입 플랜 감시",
    PENDING_ENTRY_PLAN_RECHECK: "플랜 구간 재판단",
    ENTRY_PLAN_ZONE_TOUCHED: "진입 구간 도달",
    PLAN_CANCELED_NO_ENTRY_CAPACITY: "추가 진입 여유가 없어 플랜 감시 중단",
    SETUP_TIME_PROFILE_RANGE_REVERSION_FAST: "박스권 반전 30-45분 감시",
    SETUP_TIME_PROFILE_BREAKOUT_FAST: "돌파 빠른 진입",
    SETUP_TIME_PROFILE_CONTINUATION_BALANCED: "지속형 진입 시간대 균형",
    SETUP_TIME_PROFILE_PULLBACK_FLEXIBLE: "눌림목 진입 시간 유연",
    PROVIDER_OPENAI: "OpenAI 검토",
    PROVIDER_DETERMINISTIC_MOCK: "규칙 기반 모의 판단",
    HOLDING_PROFILE_SCALP_DEFAULT: "초단기 보유 기본값",
    HOLDING_PROFILE_SWING_ALLOWED: "스윙 보유 조건 허용",
    HOLDING_PROFILE_INTRADAY_ALIGNMENT: "단기 보유 조건 정렬",
    HOLDING_PROFILE_WEAK_REGIME_SCALP_ONLY: "약한 레짐, 초단기만 허용",
    DETERMINISTIC_HARD_STOP_ACTIVE: "고정 손절 기준 활성",
    LONG_HOLDING_PROFILE_QUALITY_INSUFFICIENT: "롱 보유 품질 근거 부족",
    NO_EDGE: "거래 우위 부족",
  };
  const translated = extraReasonCodeLabelMap[value] ?? reasonCodeLabelMap[value];
  if (translated) {
    return translated;
  }
  const aiSkipCopy = describeAiSkipReason(value);
  return aiSkipCopy.known ? aiSkipCopy.title_ko : value;
}

function formatTranslatedCodeList(values: string[] | null | undefined) {
  if (!values || values.length === 0) {
    return "-";
  }
  return values.map((value) => formatInternalCodeLabelInContext(value, values)).join(", ");
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
    breadth_weak_reduce_capacity: "시장 폭 약화로 진입 여력 축소",
    score_below_threshold: "진입 점수 부족",
    low_confidence: "진입 확신도 부족",
    transition_fragile: "전환 레짐이 불안정",
    top_trader_long_crowded: "상위 트레이더 롱 쏠림",
    low_edge_hold_candidate: "우위가 약해 대기",
    ENTRY_CANDIDATE_NEUTRAL_CONTEXT_PREAI: "중립 신호라 AI 검토 생략",
    ENTRY_CANDIDATE_LOW_ACTIONABILITY_HOLD_BACKOFF: "반복 저효용 후보라 AI 검토 생략",
    ENTRY_CANDIDATE_ORDER_PATH_NOT_ACTIONABLE: "주문 경로 미준비로 AI 검토 생략",
    ENTRY_CANDIDATE_ACTIVE_PENDING_PLAN_PREAI: "기존 대기 진입안으로 AI 검토 생략",
    ENTRY_CANDIDATE_INCOMPLETE_TRADE_PLAN_PREAI: "진입 구조 불완전으로 AI 검토 생략",
    ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN: "최근 같은 장면의 AI hold 판단 재사용",
    AI_ENTRY_OUTPUT_INCOMPLETE: "AI 진입안 구조 불완전으로 hold 정규화",
    ROLE_DAILY_TOKEN_BUDGET_EXHAUSTED: "일일 AI 토큰 예산 소진",
    SOFT_SIGNAL_AI_REVIEW: "약한 후보 AI 검토 대상",
    SOFT_SIGNAL_TRANSITION_WATCH: "약한 후보 전환감시",
    SOFT_SIGNAL_REVIEW_NO_MATERIAL_CHANGE: "약한 후보 변화 부족으로 AI 생략",
    AI_CYCLE_BUDGET_EXHAUSTED: "사이클 AI 예산 초과로 신규 후보 검토 생략",
    SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_WATCH: "직접 진입 대신 대기 계획",
    SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_HOLD: "약한 후보 직접 진입 차단",
    low_confidence_pullback: "눌림목 진입 확신도 부족",
    derivatives_headwind: "파생시장 역풍",
    DERIVATIVES_ALIGNMENT_HEADWIND: "파생시장 정합성 부족",
    BREAKOUT_OI_SPREAD_FILTER: "돌파 OI/스프레드 조건 부족",
    BREAKOUT_OI_NOT_EXPANDING: "돌파 OI 확장 없음",
    EXPECTANCY_NEUTRAL: "기대값 중립",
    DERIVATIVES_NEUTRAL: "파생시장 중립",
    DERIVATIVES_HEADWIND: "파생시장 역풍",
    LEAD_MARKETS_NEUTRAL: "선행시장 중립",
    TOP_TRADER_LONG_CROWDED: "상위 트레이더 롱 쏠림",
    WEAK_BREADTH: "시장 폭 약화",
    BREADTH_WEAK_REDUCE_CAPACITY: "시장 폭 약화로 진입 여력 축소",
    WEAK_VOLUME: "거래량 확인 약함",
    MOMENTUM_WEAKENING: "모멘텀 약화",
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

function formatInternalCodeLabelInContext(value: string | null | undefined, allReasonCodes: string[] | null | undefined) {
  if (!value) {
    return "-";
  }
  const copy = describeReasonCodeInContext(value, allReasonCodes);
  if (copy.known && copy.raw_code) {
    return copy.title_ko;
  }
  return formatInternalCodeLabel(value);
}

function summarizeInternalCodeBadges(values: string[] | null | undefined) {
  const badges: InternalCodeBadge[] = [];
  const indexByLabel = new Map<string, number>();

  for (const value of values ?? []) {
    const code = value.trim();
    if (!code) {
      continue;
    }
    const label = formatInternalCodeLabel(code);
    const existingIndex = indexByLabel.get(label);
    if (existingIndex !== undefined) {
      badges[existingIndex].count += 1;
      badges[existingIndex].codes.push(code);
      continue;
    }
    indexByLabel.set(label, badges.length);
    badges.push({ key: code, label, count: 1, codes: [code] });
  }

  const visible = badges.slice(0, maxVisibleInternalCodeBadges);
  const hiddenCount = badges
    .slice(maxVisibleInternalCodeBadges)
    .reduce((total, badge) => total + badge.count, 0);

  return { visible, hiddenCount };
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
        {values.map((code, index) => (
          <span key={`${code}-${index}`} className="rounded-md bg-white px-2.5 py-1 text-xs font-medium text-slate-600">
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
  const payload =
    row.payload && typeof row.payload === "object" && !Array.isArray(row.payload)
      ? (row.payload as Record<string, unknown>)
      : null;
  return {
    id: typeof row.id === "number" ? row.id : null,
    symbol: typeof row.symbol === "string" ? row.symbol : null,
    decision_run_id: typeof row.decision_run_id === "number" ? row.decision_run_id : null,
    allowed: typeof row.allowed === "boolean" ? row.allowed : null,
    decision: typeof row.decision === "string" ? row.decision : null,
    reason_codes: Array.isArray(row.reason_codes)
      ? row.reason_codes.filter((item): item is string => typeof item === "string")
      : [],
    block_scope:
      typeof row.block_scope === "string"
        ? row.block_scope
        : typeof payload?.block_scope === "string"
          ? payload.block_scope
          : null,
    candidate_hold_reason_codes:
      Array.isArray(row.candidate_hold_reason_codes)
        ? row.candidate_hold_reason_codes.filter((item): item is string => typeof item === "string")
        : Array.isArray(payload?.candidate_hold_reason_codes)
          ? payload.candidate_hold_reason_codes.filter((item): item is string => typeof item === "string")
          : [],
    global_block_reason_codes:
      Array.isArray(row.global_block_reason_codes)
        ? row.global_block_reason_codes.filter((item): item is string => typeof item === "string")
        : Array.isArray(payload?.global_block_reason_codes)
          ? payload.global_block_reason_codes.filter((item): item is string => typeof item === "string")
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
    pending_entry_plan:
      row.pending_entry_plan && typeof row.pending_entry_plan === "object" && !Array.isArray(row.pending_entry_plan)
        ? (row.pending_entry_plan as Record<string, unknown>)
        : null,
    created_at: typeof row.created_at === "string" ? row.created_at : null,
    payload,
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
        params.set("symbol", symbol);
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
  chartSnapshots,
  chartFeatures,
  selectedSymbol,
  selectedCandleWindow,
  selectedTimeframe,
  selectedChartZoomRange,
  chartCandlesBySymbol = {},
  chartMarkers = [],
  renderAutoRefresh,
  renderCandlestickChart,
}: {
  operator: OperatorDashboardPayload;
  snapshots: Row[];
  features: Row[];
  chartSnapshots?: Row[];
  chartFeatures?: Row[];
  selectedSymbol: string;
  selectedCandleWindow: CandleWindow;
  selectedTimeframe: MarketChartTimeframe;
  selectedChartZoomRange: MarketChartZoomRange | null;
  chartCandlesBySymbol?: MarketChartCandlesBySymbol;
  chartMarkers?: MarketChartEventMarker[];
  renderAutoRefresh?: MarketAutoRefreshRenderer;
  renderCandlestickChart?: MarketCandlestickRenderer;
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
  const chartSourceSnapshots = chartSnapshots && chartSnapshots.length > 0 ? chartSnapshots : filteredSnapshots;
  const chartSourceFeatures = chartFeatures && chartFeatures.length > 0 ? chartFeatures : filteredFeatures;
  const timeframeAvailability = availableMarketTimeframes(chartSourceSnapshots, chartSourceFeatures);
  const effectiveTimeframe = resolveEffectiveMarketTimeframe(selectedTimeframe, timeframeAvailability);
  const requestedTimeframeAvailability = timeframeAvailability.get(selectedTimeframe);
  const timeframeFallbackNotice =
    selectedTimeframe !== effectiveTimeframe
      ? `${marketTimeframeLabel(selectedTimeframe)} 요청은 현재 지원 metadata가 없어 ${marketTimeframeLabel(effectiveTimeframe)} 기준으로 표시합니다. ${
          requestedTimeframeAvailability?.detail ?? "지원 정보 없음"
        }`
      : null;
  const marketFreshness = marketFreshnessDisplay(asRecord(operator.control.market_freshness_summary));
  const chartModels =
    selectedSymbol === "ALL"
      ? []
      : buildMarketChartModels(
          symbols,
          chartSourceSnapshots,
          chartSourceFeatures,
          selectedCandleWindow,
          effectiveTimeframe,
          chartCandlesBySymbol,
          selectedChartZoomRange,
          chartMarkers,
        );
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
        <div className="mt-4 grid gap-3 lg:grid-cols-3">
          {metricCard("시장 데이터 소스", marketFreshness.sourceLabel, marketFreshness.sourceHint, { compact: true })}
          {metricCard("시장 데이터 상태", marketFreshness.statusLabel, marketFreshness.statusHint, { compact: true })}
          {metricCard("실시간 캔들 스트림", marketFreshness.streamLabel, marketFreshness.streamHint, { compact: true })}
        </div>
      </section>

      {timeframeFallbackNotice ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900">
          {timeframeFallbackNotice}
        </div>
      ) : null}

      {selectedSymbol === "ALL" ? (
        <MarketSymbolComparisonGrid
          symbols={symbols}
          selectedCandleWindow={selectedCandleWindow}
          selectedTimeframe={effectiveTimeframe}
        />
      ) : (
        <MarketChartSection
          models={chartModels}
          selectedSymbol={selectedSymbol}
          selectedCandleWindow={selectedCandleWindow}
          selectedTimeframe={effectiveTimeframe}
          timeframeAvailability={timeframeAvailability}
          renderAutoRefresh={renderAutoRefresh}
          renderCandlestickChart={renderCandlestickChart}
        />
      )}

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex flex-col gap-2 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">시장 입력 요약</p>
            <h2 className="mt-2 text-xl font-semibold text-slate-950">심볼별 최신 입력 상태</h2>
          </div>
          <p className="text-sm text-slate-500">차트와 같은 백엔드 스냅샷 기준으로 표시합니다.</p>
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
                    ? `시장 스냅샷은 수집됐지만 지표 입력 생성이 ${symbol.feature_input_delay_minutes ?? "-"}분째 지연되고 있습니다. 봉 기준 ${symbol.timeframe ?? "-"} 예상 대기 ${symbol.feature_input_delay_threshold_minutes ?? "-"}분을 넘겼습니다.`
                    : "시장 스냅샷은 수집됐지만 피처 입력은 아직 생성되지 않았습니다."}
                </div>
              ) : null}
            </div>
          )})}
        </div>
      </section>

      <MarketRawDataPanel selectedSymbol={selectedSymbol} selectedTimeframe={effectiveTimeframe} />
    </div>
  );
}

export function DecisionView({
  operator,
  decisionRows,
  selectedSymbol,
  entryFlowTab = "summary",
}: {
  operator: OperatorDashboardPayload;
  decisionRows: Row[];
  selectedSymbol: string;
  entryFlowTab?: EntryLifecycleTab;
}) {
  const symbol =
    operator.symbols.find((item) => item.symbol === selectedSymbol) ?? operator.symbols[0] ?? null;
  const filteredDecisionRows = decisionRows.filter(
    (row) => String(row.symbol ?? "").toUpperCase() === (symbol?.symbol ?? ""),
  );
  const recommendation = symbol ? summarizeLastAiRecommendation(symbol) : null;
  const currentCycle = symbol ? summarizeCurrentCycleSelection(symbol) : null;
  const execution = symbol ? summarizeExecutionState(symbol) : null;
  const positionStatus = symbol ? positionStatusText(symbol) : null;
  const historicalGapNotice = symbol ? describeHistoricalDecisionGap(symbol) : null;
  const topReview = symbol ? aiReviewSummary(symbol) : null;
  const topTriggerPresentation = symbol ? describeAiTriggerReason(getAiTriggerReason(symbol)) : null;
  const topAiReviewLabel = symbol ? aiReviewTypeLabel(symbol) : "-";
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
        <div className="mt-5 grid gap-4 lg:grid-cols-5">
          {metricCard("마지막 AI 스냅샷", formatDateTime(symbol.ai_decision.created_at), "상단 AI 카드는 과거 스냅샷일 수 있습니다.")}
          {metricCard("이번 주기 AI 상태", topReview?.label ?? "-", topReview?.detail ?? "-")}
          {metricCard(
            "최근 AI 호출 시각",
            formatDateTime(symbol.ai_decision.last_ai_invoked_at),
            topTriggerPresentation?.legacy
              ? `사유 ${topTriggerPresentation.label} / 현재 런타임 트리거가 아니라 저장된 과거 정책 기록입니다.`
              : `검토 분류 ${topAiReviewLabel}`,
          )}
          {metricCard(
            "이번 판단 주기 기준",
            formatDateTime(operator.generated_at),
            "현재 대시보드 새로고침 기준으로 후보 선정과 실행 상태를 보여줍니다.",
          )}
        </div>
        <EntryLifecycleTabs operator={operator} symbol={symbol} activeTab={entryFlowTab} />
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
  const schedulerFreshness = operator.control.scheduler_freshness_summary ?? {};
  const schedulerFreshnessStatus =
    schedulerFreshness.status === "stale"
      ? "지연"
      : schedulerFreshness.status === "fresh"
        ? "정상"
        : "미확인";
  const schedulerFreshnessHint =
    typeof schedulerFreshness.message === "string" ? schedulerFreshness.message : "scheduler_runs 기준 최신성";

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
          {metricCard("최신성", schedulerFreshnessStatus, schedulerFreshnessHint)}
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
          보호 상태 점검은 AI가 보호 주문을 직접 생성/복구한다는 뜻이 아니라, 별도 규칙 기반 보호 복구 상태와 분리해서 봅니다.
        </p>
        <div className="mt-5 grid gap-4 xl:grid-cols-3">
          {operator.symbols.map((symbol) => {
            const aiReview = aiReviewReadModel(symbol);
            const review = aiReviewSummary(symbol);
            const triggerPresentation = describeAiTriggerReason(getAiTriggerReason(symbol));
            const reviewLabel = aiReviewTypeLabel(symbol);
            const reviewHint = aiReviewReasonHint(symbol);
            const skipReason = getAiSkipReason(symbol);
            const providerStatus = asNonEmptyString(aiReview?.provider_status);
            const reviewSkipped = Boolean(
              skipReason ||
                aiReview?.provider_skipped === true ||
                aiReview?.trigger_deduped === true ||
                symbol.ai_decision.trigger_deduped ||
                providerStatus === "skipped_pre_ai" ||
                providerStatus === "deduped",
            );
            const reviewInvoked = Boolean(
              aiReview?.provider_invoked === true ||
                providerStatus === "invoked" ||
                (!providerStatus && symbol.ai_decision.last_ai_invoked_at),
            );
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
                        : reviewSkipped
                          ? "neutral"
                          : reviewInvoked
                          ? "good"
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
                    formatTranslatedCodeList(symbolRiskReasonCodes(symbol)),
                    internalCodeHint(
                      symbolRiskReasonCodes(symbol),
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

export function OrdersView({
  orderRows,
  executionRows,
  activeTab,
  selectedSymbol = null,
  selectedPositionId = null,
}: {
  orderRows: Row[];
  executionRows: Row[];
  activeTab: OrderLifecycleTab;
  selectedSymbol?: string | null;
  selectedPositionId?: number | null;
}) {
  const normalizedSelectedSymbol = selectedSymbol?.trim().toUpperCase() || null;
  const allGroups = buildPositionHistoryGroups(orderRows, executionRows);
  const groups = allGroups.filter((group) => {
    const matchesPosition = selectedPositionId === null || group.positionId === selectedPositionId;
    const matchesSymbol = normalizedSelectedSymbol === null || group.symbol.toUpperCase() === normalizedSelectedSymbol;
    return matchesPosition && matchesSymbol;
  });
  const activeTabMeta = orderLifecycleTabs.find((tab) => tab.key === activeTab) ?? orderLifecycleTabs[0];
  const orderCount = groups.reduce((total, group) => total + group.orders.length, 0);
  const executionCount = groups.reduce((total, group) => total + group.executions.length, 0);
  const hasFilter = selectedPositionId !== null || normalizedSelectedSymbol !== null;
  const filterLabel =
    selectedPositionId !== null && normalizedSelectedSymbol
      ? `position #${selectedPositionId} / ${normalizedSelectedSymbol}`
      : selectedPositionId !== null
        ? `position #${selectedPositionId}`
        : normalizedSelectedSymbol ?? null;

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">주문 / 체결</p>
            <h2 className="mt-2 text-xl font-semibold text-slate-950">포지션 단위 실행 이력</h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              같은 position_id에서 발생한 진입 주문, 보호 주문, execution row를 한 묶음으로 표시합니다. 포지션 연결이
              없는 행은 주문 ID 기준으로 별도 묶음에 남깁니다.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {filterLabel ? (
              <div className="w-fit rounded-md border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
                필터 {filterLabel}
              </div>
            ) : null}
            <div className="w-fit rounded-md bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
              묶음 {groups.length}개 / 주문 {orderCount}건 / 체결 {executionCount}건
            </div>
          </div>
        </div>
        <div className="mt-5">
          <OrderLifecycleTabLinks
            activeTab={activeTab}
            selectedSymbol={normalizedSelectedSymbol}
            selectedPositionId={selectedPositionId}
          />
        </div>
        <p className="mt-3 text-xs leading-5 text-slate-500">{activeTabMeta.description}</p>
      </section>

      {groups.length === 0 ? (
        <section className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-sm text-slate-500">
          <p className="font-semibold text-slate-700">표시할 주문/체결 이력이 없습니다.</p>
          <p className="mt-2 leading-6">
            {hasFilter && filterLabel
              ? `${filterLabel} 조건에 맞는 live 주문과 execution row가 없습니다.`
              : "저장된 live 주문과 execution row가 아직 없습니다."}
          </p>
        </section>
      ) : (
        <section className="space-y-4">
          {groups.map((group) => {
            const status = positionHistoryStatus(group);
            const selectedGroup = selectedPositionId !== null && group.positionId === selectedPositionId;
            return (
              <article
                key={group.key}
                className={`rounded-lg border p-4 sm:p-5 ${
                  selectedGroup ? "border-blue-300 bg-blue-50" : "border-slate-200 bg-slate-50"
                }`}
              >
                <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                  <div className="min-w-0">
                    <h3 className="text-base font-semibold text-slate-950">
                      {group.symbol} / {group.positionId !== null ? `position #${group.positionId}` : "position 미연결"}
                    </h3>
                    <p className="mt-1 text-xs leading-5 text-slate-500">
                      시작 {formatDateTime(group.createdAt)} / 최근 갱신 {formatDateTime(group.updatedAt)}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(status.kind)}`}>
                      {status.label}
                    </span>
                    <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
                      주문 {group.orders.length} / 체결 {group.executions.length}
                    </span>
                    {group.positionId !== null ? (
                      <Link
                        href={ordersViewHref({ tab: activeTab, positionId: group.positionId })}
                        className={`rounded-full border px-3 py-1 text-xs font-semibold ${
                          selectedGroup
                            ? "border-blue-300 bg-white text-blue-700"
                            : "border-slate-200 bg-white text-slate-700 hover:border-blue-200 hover:bg-blue-50"
                        }`}
                      >
                        {selectedGroup ? "선택된 position" : "이 position만 보기"}
                      </Link>
                    ) : null}
                  </div>
                </div>
                <p className="mt-3 text-xs leading-5 text-slate-600">{status.detail}</p>

                <div className="mt-4">
                  {activeTab === "summary" ? (
                    <PositionHistorySummaryPanel group={group} />
                  ) : activeTab === "orders" ? (
                    <PositionHistoryOrdersPanel group={group} />
                  ) : (
                    <PositionHistoryExecutionsPanel group={group} />
                  )}
                </div>
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}

export function PositionsView({ positionRows }: { positionRows: Row[] }) {
  const positions = positionRows.map(asPositionRow);

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">실거래 기준</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">열린 포지션과 보호 가격</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          진입가, 현재가, 손절가, 목표가, 손익과 보호 주문 상태를 함께 확인합니다. 보호 가격이 없으면 주문 관리 전에 먼저
          확인해야 합니다.
        </p>
      </section>

      {positions.length === 0 ? (
        <section className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-sm text-slate-500">
          <p className="font-semibold text-slate-700">열린 포지션이 없습니다.</p>
          <p className="mt-2 leading-6">실거래 기준 현재 보유 중인 포지션이 없습니다.</p>
        </section>
      ) : (
        <section className="grid gap-4 xl:grid-cols-2">
          {positions.map((row, index) => {
            const protectionTone = positionProtectionTone(row);
            const protectionLabel = row.protected ? "보호됨" : "보호 확인 필요";
            const sideLabel = formatPositionSide(row.side);
            const notional =
              row.quantity !== null && row.markPrice !== null ? Math.abs(row.quantity * row.markPrice) : null;
            const riskReward = formatPositionRiskReward(row);
            const stopDistance = formatDistancePercent(row.markPrice, row.stopLoss);
            const targetDistance = formatDistancePercent(row.markPrice, row.takeProfit);
            const missingProtection =
              row.missingComponents.length > 0 ? row.missingComponents.join(", ") : "누락 없음";
            const orderSummary =
              row.orderIds.length > 0 ? row.orderIds.map((id) => `#${id}`).join(", ") : "보호 주문 ID 없음";

            return (
              <article
                key={row.id ?? `${row.symbol}-${row.openedAt ?? index}`}
                className="rounded-lg border border-slate-200 bg-slate-50 p-4"
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <h3 className="text-base font-semibold text-slate-950">
                      {row.symbol} {sideLabel}
                    </h3>
                    <p className="mt-1 text-xs text-slate-500">
                      ID {row.id ?? "-"} / 진입 {formatDateTime(row.openedAt)} / 갱신 {formatDateTime(row.updatedAt)}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <span className="rounded-md bg-white px-3 py-1 text-xs font-medium text-slate-600">
                      {formatDisplayValue(row.status ?? "-", "status")}
                    </span>
                    <span className="rounded-md bg-white px-3 py-1 text-xs font-medium text-slate-600">
                      {formatDisplayValue(row.mode ?? "-", "mode")}
                    </span>
                    <span className={`rounded-md border px-3 py-1 text-xs font-semibold ${badgeClass(protectionTone)}`}>
                      {protectionLabel}
                    </span>
                    <Link
                      href={row.id !== null ? ordersViewHref({ positionId: row.id }) : ordersViewHref({ symbol: row.symbol })}
                      className="rounded-md border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700 hover:border-blue-300 hover:bg-blue-100"
                    >
                      주문/체결 추적
                    </Link>
                  </div>
                </div>

                <div className="mt-4 grid gap-3 lg:grid-cols-2">
                  {metricCard(
                    "진입가 / 현재가",
                    `${formatPriceValue(row.entryPrice)} / ${formatPriceValue(row.markPrice)}`,
                    "평균 진입가와 최신 평가가",
                  )}
                  {metricCard(
                    "수량 / 평가 금액",
                    `${formatNumber(row.quantity, 6)} / ${formatUsdtValue(notional, 2)}`,
                    row.leverage !== null ? `레버리지 ${formatNumber(row.leverage, 2)}x` : "레버리지 정보 없음",
                  )}
                  {metricCard(
                    "손절가",
                    formatPriceValue(row.stopLoss),
                    row.hasStopLoss ? `현재가 기준 ${stopDistance} 거리` : "손절 주문이 확인되지 않았습니다.",
                  )}
                  {metricCard(
                    "목표가",
                    formatPriceValue(row.takeProfit),
                    row.hasTakeProfit ? `현재가 기준 ${targetDistance} 거리` : "익절 주문이 확인되지 않았습니다.",
                  )}
                  {metricCard("진입 기준 보상/위험", riskReward.value, riskReward.hint)}
                  <div className="rounded-md border border-slate-200 bg-white p-4">
                    <p className="text-xs font-medium text-slate-500">미실현 / 실현 손익</p>
                    <p className={`mt-2 text-xl font-semibold leading-7 ${positionPnlClass(row.unrealizedPnl)}`}>
                      {formatUsdtValue(row.unrealizedPnl, 4)} / {formatUsdtValue(row.realizedPnl, 4)}
                    </p>
                    <p className="mt-2 break-words text-xs leading-5 text-slate-500">USDT 기준 포지션 손익</p>
                  </div>
                  {metricCard(
                    "보호 주문",
                    `${row.protectiveOrderCount ?? 0}개`,
                    `손절 ${row.hasStopLoss ? "있음" : "없음"} / 익절 ${row.hasTakeProfit ? "있음" : "없음"} / ${orderSummary}`,
                    { compact: true },
                  )}
                  {metricCard("누락 보호 항목", missingProtection, "비어 있으면 현재 보호 기준은 충족된 상태입니다.", {
                    compact: true,
                  })}
                </div>
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}

function CompactRiskProfileSummary({
  executionProfile,
}: {
  executionProfile: ReturnType<typeof buildExecutionRiskProfileSummary>;
}) {
  if (!executionProfile) {
    return null;
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">실행 리스크 프로파일</p>
          <h2 className="mt-2 text-xl font-semibold text-slate-950">최종 실행 프로파일</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            {executionProfile.finalActiveProfileLabel} / {executionProfile.applicationLabel}
          </p>
        </div>
        <div className="grid gap-3 text-sm text-slate-700 sm:grid-cols-3 lg:min-w-[520px]">
          <p>
            <span className="block text-xs font-semibold text-slate-500">AI 추천</span>
            {executionProfile.aiRecommendedProfile}
          </p>
          <p>
            <span className="block text-xs font-semibold text-slate-500">신규 진입</span>
            {executionProfile.newEntryLabel}
          </p>
          <p>
            <span className="block text-xs font-semibold text-slate-500">생존 경로</span>
            {executionProfile.survivalPathLabel}
          </p>
        </div>
      </div>
    </section>
  );
}

function CompactAlertList({ rows }: { rows: Row[] }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">리스크 관련 알림</p>
          <h2 className="mt-1 text-xl font-semibold text-slate-950 sm:text-2xl">운영 알림</h2>
        </div>
        <div className="w-fit rounded-md bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
          {rows.length}건
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-sm text-slate-500">
          <p className="font-semibold text-slate-700">표시할 알림이 없습니다.</p>
          <p className="mt-2 leading-6">최근 리스크 관련 알림 기록이 없습니다.</p>
        </div>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {rows.map((row, index) => {
            const title = rowString(row, "title") ?? `알림 ${index + 1}`;
            const message = rowString(row, "message") ?? "-";
            const severity = rowString(row, "severity") ?? rowString(row, "level") ?? rowString(row, "status");
            const createdAt = rowString(row, "created_at");
            const severityKey = severity?.toLowerCase() ?? "";
            const tone = severityKey.includes("error") || severityKey.includes("critical")
              ? "danger"
              : severityKey.includes("warn")
                ? "warn"
                : "neutral";
            return (
              <article
                key={`${rowString(row, "id") ?? title}-${createdAt ?? index}`}
                className="rounded-lg border border-slate-200 bg-slate-50 p-4"
              >
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <h3 className="text-sm font-semibold text-slate-950">{title}</h3>
                    <p className="mt-1 text-xs text-slate-500">{formatDateTime(createdAt)}</p>
                  </div>
                  {severity ? (
                    <span className={`w-fit rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(tone)}`}>
                      {severity}
                    </span>
                  ) : null}
                </div>
                <p className="mt-3 text-sm leading-6 text-slate-700">{message}</p>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

export function RiskView({
  operator,
  riskRows,
  alertRows,
  aiUsage,
  summaryMode = false,
}: {
  operator?: OperatorDashboardPayload | null;
  riskRows: Row[];
  alertRows: Row[];
  aiUsage?: AIUsagePayload | null;
  summaryMode?: boolean;
}) {
  const rawRows = riskRows.map(asRiskCheckRow);
  const operatorSymbolsByName = operatorSymbolMap(operator);
  const rows = riskDisplayRows(rawRows, operatorSymbolsByName);
  const collapsedRowCount = Math.max(0, rawRows.length - rows.length);
  const executionProfile = operator ? buildExecutionRiskProfileSummary(operator.control) : null;

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">리스크 점검</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">플랜, AI 검토, 리스크 승인 흐름 확인</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          저장된 진입 플랜이 있으면 대기 구간과 도달 시 처리 흐름을 먼저 보여줍니다. 플랜이 없거나 AI 검토가 생략된 경우에는
          왜 주문이 나가지 않았는지와 다음 판단 조건을 우선 표시합니다.
        </p>
      </section>

      {summaryMode ? <RiskAIUsageSummary usage={aiUsage} /> : null}

      {executionProfile ? summaryMode ? (
        <CompactRiskProfileSummary executionProfile={executionProfile} />
      ) : (
        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
          <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">실행 리스크 프로파일</p>
              <h2 className="mt-2 text-xl font-semibold text-slate-950">AI 추천과 최종 실행 프로파일</h2>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                백엔드 운영 응답값 기준입니다. 프론트는 기본 규칙, AI 추천, 최종 프로파일을 자체 계산하지 않습니다.
              </p>
            </div>
            <span
              className={`w-fit rounded-md border px-3 py-1 text-xs font-semibold ${
                operator?.control.profile_new_entry_blocked ? badgeClass("danger") : badgeClass("neutral")
              }`}
            >
              {executionProfile.newEntryLabel}
            </span>
          </div>
          <div className="grid gap-3 lg:grid-cols-4">
            {metricCard("기본 규칙", executionProfile.deterministicProfile, "규칙 기반 시장 상태")}
            {metricCard(
              "AI 추천",
              executionProfile.aiRecommendedProfile,
              `${executionProfile.recommendationStatusLabel} / ID ${executionProfile.recommendationId}`,
            )}
            {metricCard(
              "최종 적용",
              executionProfile.finalActiveProfileLabel,
              executionProfile.shadowFinalProfile !== "-"
                ? `관찰 모드 예상값 ${executionProfile.shadowFinalProfile}`
                : executionProfile.selectedReason,
            )}
            {metricCard("적용 방식", executionProfile.selectionMode, executionProfile.applicationLabel)}
          </div>
          <div className="mt-4 grid gap-3 lg:grid-cols-4">
            {metricCard("AI 적용 여부", executionProfile.applicationLabel, executionProfile.applicationDetail, {
              compact: true,
            })}
            {metricCard("신규 진입", executionProfile.newEntryLabel, executionProfile.newEntryDetail, {
              compact: true,
            })}
            {metricCard("프로파일 차단", executionProfile.profileBlockLabel, executionProfile.profileBlockDetail, {
              compact: true,
            })}
            {metricCard(
              "reduce/exit/emergency_exit",
              executionProfile.survivalPathLabel,
              executionProfile.survivalPathDetail,
              { compact: true },
            )}
          </div>
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            {metricCard(
              "추천 유효기간 / 다음 검토",
              `${formatDateTime(executionProfile.validUntil)} / ${formatDateTime(executionProfile.nextReviewAt)}`,
              `신뢰도 ${executionProfile.confidenceLabel}`,
              { compact: true },
            )}
            {metricCard(
              "무시/완화 차단 사유",
              [
                ...executionProfile.ignoredReasonCodes,
                ...executionProfile.relaxationBlockReasonCodes,
              ].join(", ") || "-",
              executionProfile.isProfileSelectorShadow
                ? "관찰 모드: AI 추천은 실제 차단처럼 적용되지 않습니다."
                : "백엔드 프로파일 선택기가 내려준 사유 코드",
              { compact: true },
            )}
          </div>
        </section>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">리스크 카드</p>
            <h2 className="mt-2 text-xl font-semibold text-slate-950">최근 리스크 점검</h2>
          </div>
          <div className="w-fit rounded-md bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
            대표 {rows.length}건{collapsedRowCount > 0 ? ` / 내부 ${collapsedRowCount}건 묶음` : ""}
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
              const skipCopy = aiSkipReasonCopy(skipReason);
              const macroEventContext = riskMacroEventContext(row);
              const macroEventSummary = formatMacroEventContextSummary(macroEventContext);
              const macroEventDetail = formatMacroEventContextDetail(macroEventContext);
              const symbolLabel = row.symbol ?? `리스크 ${index + 1}`;
              const decisionRunLabel =
                row.decision_run_id !== null ? `decision #${row.decision_run_id}` : "linked decision 없음";
              const triggerReasonHint = riskTriggerReasonHint(row, decisionRunLabel);
              const matchedSymbol = operatorSymbolsByName.get(row.symbol ?? "") ?? null;
              const plan = riskEntryPlanDetails(row, matchedSymbol);
              const status = riskRowStatusPresentation(row, plan);
              const riskDecision = riskDecisionPresentation(row, plan);
              const noTradeReason = riskNoTradeReason(row, plan, skipReason ? skipCopy.detail : allowed.hint);
              const reasonEvidence = riskReasonEvidence(row);
              const aiHoldBaselineDisagreement = isAiHoldBaselineDisagreement(row);
              const riskScope = resolveRiskBlockScope(
                row.block_scope,
                row.reason_codes,
                row.candidate_hold_reason_codes,
                row.global_block_reason_codes,
              );
              const blockPresentation = riskScopePresentation(
                riskScope.scope,
                riskScope.candidateHoldReasonCodes,
                riskScope.globalBlockReasonCodes,
              );
              const displayReasonCodes =
                riskScope.scope === "candidate_hold"
                  ? riskScope.candidateHoldReasonCodes
                  : riskScope.scope === "global_block"
                    ? riskScope.globalBlockReasonCodes
                    : riskScope.scope === "mixed"
                      ? dedupeNonEmptyReasons([...riskScope.globalBlockReasonCodes, ...riskScope.candidateHoldReasonCodes])
                      : row.reason_codes;
              const internalReasonTitle = aiHoldBaselineDisagreement
                ? "내부 검증 코드"
                : riskScope.scope === "candidate_hold"
                  ? "후보 관망 사유"
                  : "차단 사유";
              const internalReasonHint = aiHoldBaselineDisagreement
                ? "AI 관망 상태와 함께 저장된 리스크 원본 코드입니다. 실제 주문은 리스크 승인 없이는 제출되지 않습니다."
                : riskScope.scope === "candidate_hold"
                  ? "전역 거래 차단이 아니라 이번 후보 품질 부족 또는 트리거 미충족에 따른 관망 사유입니다."
                  : reasonEvidence || internalCodeHint(displayReasonCodes);

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
                      className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(status.kind)}`}
                    >
                      {status.label}
                    </span>
                  </div>

                  <div className={`mt-4 rounded-md border px-4 py-3 ${statusPanelClass(status.kind)}`}>
                    <p className="text-xs font-semibold">현재 상태</p>
                    <p className="mt-1 text-lg font-semibold leading-7">{status.label}</p>
                    <p className="mt-2 text-sm leading-6">{status.detail}</p>
                    <div className="mt-3 rounded-md bg-white/60 px-3 py-2 text-sm leading-6">
                      <p className="text-xs font-semibold">다음 동작</p>
                      <p className="mt-1">{status.nextStep}</p>
                    </div>
                  </div>

                  <div className="mt-4 grid gap-3 lg:grid-cols-2">
                    {metricCard("대기 플랜", plan.label, plan.detail, { compact: true })}
                    {summaryMode ? null : metricCard("진입 구간", plan.zoneText, plan.distanceText, { compact: true })}
                    {summaryMode ? null : metricCard("도달 시 처리", plan.actionTitle, plan.actionDetail, { compact: true })}
                    {metricCard(
                      "거래 안 된 이유",
                      noTradeReason.label,
                      noTradeReason.hint,
                      { compact: true },
                    )}
                    {metricCard("차단 구분", blockPresentation.label, blockPresentation.hint, { compact: true })}
                    {summaryMode ? metricCard("AI 최종 판단", riskDecision.label, riskDecision.hint, { compact: true }) : null}
                    {summaryMode ? metricCard("허용 여부", allowed.label, allowed.hint, { compact: true }) : null}
                    {summaryMode
                      ? null
                      : metricCard("AI 최종 판단", riskDecision.label, riskDecision.hint)}
                    {summaryMode ? null : metricCard("허용 여부", allowed.label, allowed.hint)}
                    {summaryMode
                      ? null
                      : metricCard(
                          "무효화 / 만료",
                          `${plan.invalidationText} / ${plan.expiresText}`,
                          "플랜이 있을 때만 의미 있는 관리 기준",
                          { compact: true },
                        )}
                    {summaryMode
                      ? null
                      : metricCard(
                          "승인 리스크 / 레버리지",
                          `${formatRatio(row.approved_risk_pct)} / ${
                            row.approved_leverage !== null ? `${formatNumber(row.approved_leverage, 2)}x` : "-"
                          }`,
                          "허용된 경우에만 의미 있는 승인 수치",
                        )}
                  </div>

                  {summaryMode ? (
                    <div className="mt-4 rounded-md border border-slate-200 bg-white px-4 py-3 text-sm leading-6 text-slate-700">
                      <p>
                        <span className="font-semibold text-slate-950">{internalReasonTitle}</span>:{" "}
                        {formatTranslatedCodeList(displayReasonCodes)}
                      </p>
                      <p className="mt-1 text-xs leading-5 text-slate-500">{reasonEvidence || triggerReason}</p>
                    </div>
                  ) : (
                    <>
                      <div className="mt-4 grid gap-3 lg:grid-cols-2">
                        {metricCard("AI 호출 분류", triggerReason, triggerReasonHint, { compact: true })}
                        {metricCard(
                          "AI 검토 생략",
                          translateAiSkipReason(skipReason),
                          skipReason ? skipCopy.nextStep : "생략 사유 없음",
                          { compact: true },
                        )}
                        {metricCard(
                          "당시 시장 신호 요약",
                          triggerSummary,
                          "AI 호출 원인이 아니라 판단 당시 지표 요약입니다.",
                          { compact: true },
                        )}
                        {metricCard("이벤트 리스크", macroEventSummary, macroEventDetail, { compact: true })}
                        {metricCard(
                          internalReasonTitle,
                          formatTranslatedCodeList(displayReasonCodes),
                          internalReasonHint,
                          { compact: true },
                        )}
                        {metricCard(
                          "판단 기록 ID",
                          row.decision_run_id !== null ? String(row.decision_run_id) : "-",
                          row.decision_run_id !== null ? "연결된 decision row" : "linked decision 없음",
                          { compact: true },
                        )}
                        {metricCard("생성 시각", formatDateTime(row.created_at), "리스크 점검 기록 생성 시각", {
                          compact: true,
                        })}
                      </div>

                      <div className="mt-4">
                        <p className="mb-2 text-xs font-medium text-slate-500">리스크 사유 그룹</p>
                        <RiskReasonGroups reasons={displayReasonCodes} />
                      </div>
                    </>
                  )}

                  {summaryMode ? (
                    <RiskCheckPayloadDetails riskCheckId={row.id ?? null} />
                  ) : row.payload ? (
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

      {summaryMode ? (
        <CompactAlertList rows={alertRows} />
      ) : (
        <DataTable
          title="운영 알림"
          description="리스크 관련 알림"
          rows={alertRows}
          emptyStateTitle="표시할 알림이 없습니다."
          emptyStateDescription="최근 리스크 관련 알림 기록이 없습니다."
        />
      )}
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

