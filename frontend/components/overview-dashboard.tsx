"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ALL_SYMBOLS, filterSymbolsBySelection, resolveSelectedSymbol } from "../lib/selected-symbol";
import { type EventOperatorControlPayload } from "../lib/event-operator-control.js";
import { buildOperatorDetailSections, type OperatorDetailTone } from "../lib/operator-symbol-detail";
import { lookupRiskReasonCode } from "../lib/risk-reason-copy.js";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const refreshIntervalMs = 15000;
const rolloutModeOptions = ["paper", "shadow", "live_dry_run", "limited_live", "full_live"] as const;

type RolloutMode = (typeof rolloutModeOptions)[number];

type PerformanceEntry = {
  key: string;
  holds: number;
  wins: number;
  losses: number;
  net_realized_pnl_total: number;
  average_slippage_pct: number;
};

type PerformanceWindow = {
  window_label: string;
  summary: {
    decisions: number;
    approvals: number;
    holds: number;
    wins: number;
    losses: number;
    net_realized_pnl_total: number;
    fee_total: number;
  };
  rationale_winners: PerformanceEntry[];
  rationale_losers: PerformanceEntry[];
  top_regimes: PerformanceEntry[];
  top_symbols: PerformanceEntry[];
  top_hold_conditions: PerformanceEntry[];
};

type ExecutionWindow = {
  window: string;
  execution_quality_summary: Record<string, number>;
};

type AuditEvent = {
  event_category: string;
  event_type: string;
  entity_type: string;
  entity_id: string;
  severity: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
};

type SyncScopeStatus = {
  status?: string;
  last_sync_at?: string | null;
  freshness_seconds?: number | null;
  stale_after_seconds?: number | null;
  stale?: boolean;
  incomplete?: boolean;
  last_failure_reason?: string | null;
};

type ControlStatusSummary = {
  exchange_can_trade: boolean | null;
  rollout_mode: RolloutMode;
  exchange_submit_allowed: boolean;
  limited_live_max_notional: number | null;
  app_live_armed: boolean;
  approval_window_open: boolean;
  approval_state?: string | null;
  approval_detail?: Record<string, unknown>;
  paused: boolean;
  degraded: boolean;
  risk_allowed: boolean | null;
  blocked_reasons_current_cycle: string[];
};

type OperatorDecisionSnapshot = {
  decision_run_id: number | null;
  created_at: string | null;
  provider_name: string | null;
  trigger_event: string | null;
  status: string | null;
  summary: string | null;
  symbol: string | null;
  timeframe: string | null;
  decision: string | null;
  confidence: number | null;
  rationale_codes: string[];
  explanation_short: string | null;
  holding_profile: string | null;
  holding_profile_reason: string | null;
  assigned_slot: string | null;
  candidate_weight: number | null;
  capacity_reason: string | null;
  portfolio_slot_soft_cap_applied: boolean;
  last_ai_trigger_reason: string | null;
  last_ai_invoked_at: string | null;
  next_ai_review_due_at: string | null;
  trigger_deduped: boolean;
  trigger_fingerprint: string | null;
  last_ai_skip_reason: string | null;
  event_risk_acknowledgement: string | null;
  confidence_penalty_reason: string | null;
  scenario_note: string | null;
};

type OperatorRiskSnapshot = {
  risk_check_id: number | null;
  decision_run_id: number | null;
  snapshot_id: number | null;
  as_of: string | null;
  created_at: string | null;
  allowed: boolean | null;
  decision: string | null;
  operating_state: string | null;
  reason_codes: string[];
  blocked_reason_codes: string[];
  adjustment_reason_codes: string[];
  approved_risk_pct: number | null;
  approved_leverage: number | null;
  raw_projected_notional: number | null;
  approved_projected_notional: number | null;
  approved_quantity: number | null;
  auto_resized_entry: boolean;
  size_adjustment_ratio: number | null;
  auto_resize_reason: string | null;
  holding_profile: string | null;
  holding_profile_reason: string | null;
  assigned_slot: string | null;
  candidate_weight: number | null;
  capacity_reason: string | null;
  portfolio_slot_soft_cap_applied: boolean;
  exposure_headroom_snapshot: Record<string, number>;
  debug_payload: Record<string, unknown>;
};

type ExecutionFillSummary = {
  execution_id: number | null;
  order_id: number | null;
  external_trade_id: string | null;
  created_at: string | null;
  status: string | null;
  fill_price: number | null;
  fill_quantity: number | null;
  fee_paid: number | null;
  commission_asset: string | null;
  realized_pnl: number | null;
};

type OperatorExecutionSnapshot = {
  order_id: number | null;
  execution_id: number | null;
  decision_run_id: number | null;
  created_at: string | null;
  execution_created_at: string | null;
  symbol: string | null;
  side: string | null;
  order_type: string | null;
  order_status: string | null;
  execution_status: string | null;
  requested_quantity: number | null;
  filled_quantity: number | null;
  average_fill_price: number | null;
  fill_price: number | null;
  reason_codes: string[];
  execution_quality: Record<string, unknown>;
  recent_fills: ExecutionFillSummary[];
};

type OperatorPositionSummary = {
  is_open: boolean;
  position_id: number | null;
  side: string | null;
  status: string | null;
  quantity: number | null;
  entry_price: number | null;
  mark_price: number | null;
  unrealized_pnl: number | null;
  realized_pnl: number | null;
  leverage: number | null;
  holding_profile: string | null;
  holding_profile_reason: string | null;
  initial_stop_type: string | null;
  ai_stop_management_allowed: boolean | null;
  hard_stop_active: boolean | null;
  stop_widening_allowed: boolean | null;
};

type OperatorCandidateSelectionSnapshot = {
  symbol: string | null;
  selected: boolean | null;
  selection_reason: string | null;
  selected_reason: string | null;
  rejected_reason: string | null;
  strategy_engine: string | null;
  holding_profile: string | null;
  holding_profile_reason: string | null;
  assigned_slot: string | null;
  candidate_weight: number | null;
  capacity_reason: string | null;
  blocked_reason_codes: string[];
  portfolio_slot_soft_cap_applied: boolean;
};

type OperatorProtectionSummary = {
  status: string;
  protected: boolean;
  protective_order_count: number;
  has_stop_loss: boolean;
  has_take_profit: boolean;
  missing_components: string[];
  recovery_status?: string | null;
  auto_recovery_active: boolean;
  failure_count: number;
  last_error?: string | null;
  last_transition_at?: string | null;
  trigger_source?: string | null;
  lifecycle_state?: string | null;
  verification_status?: string | null;
  last_event_type?: string | null;
  last_event_message?: string | null;
  last_event_at?: string | null;
};

type OperatorSymbolSummary = {
  symbol: string;
  timeframe: string | null;
  latest_price: number | null;
  market_snapshot_time: string | null;
  market_candle_time: string | null;
  feature_input_delay_minutes: number | null;
  feature_input_delay_threshold_minutes: number | null;
  feature_input_delayed: boolean;
  market_context_summary: Record<string, unknown>;
  derivatives_summary: Record<string, unknown>;
  event_context_summary: Record<string, unknown>;
  event_operator_control?: EventOperatorControlPayload | null;
  ai_decision: OperatorDecisionSnapshot;
  risk_guard: OperatorRiskSnapshot;
  execution: OperatorExecutionSnapshot;
  open_position: OperatorPositionSummary;
  protection_status: OperatorProtectionSummary;
  candidate_selection: OperatorCandidateSelectionSnapshot;
  blocked_reasons: string[];
  live_execution_ready: boolean;
  stale_flags: string[];
  last_updated_at: string | null;
  audit_events: AuditEvent[];
};

export type OperatorDashboardPayload = {
  generated_at: string;
  control: {
    can_enter_new_position: boolean;
    mode: string;
    rollout_mode: RolloutMode;
    exchange_submit_allowed: boolean;
    limited_live_max_notional: number | null;
    default_symbol: string;
    default_timeframe: string;
    tracked_symbols: string[];
    tracked_symbol_count: number;
    live_trading_enabled: boolean;
    live_execution_ready: boolean;
    approval_armed: boolean;
    approval_expires_at: string | null;
    trading_paused: boolean;
    operating_state: string;
    guard_mode_reason_message: string | null;
    pause_reason_code: string | null;
    pause_origin: string | null;
    auto_resume_status: string;
    auto_resume_eligible: boolean;
    auto_resume_after: string | null;
    auto_resume_last_blockers: string[];
    latest_blocked_reasons: string[];
    control_status_summary?: ControlStatusSummary | null;
    sync_freshness_summary: Record<string, SyncScopeStatus>;
    protection_recovery_status: string;
    protected_positions: number;
    unprotected_positions: number;
    open_positions: number;
    pnl_summary: Record<string, unknown>;
    daily_pnl: number;
    cumulative_pnl: number;
    account_sync_summary: Record<string, unknown>;
    exposure_summary: Record<string, unknown>;
    scheduler_status: string | null;
    scheduler_window: string | null;
    scheduler_next_run_at: string | null;
  };
  symbols: OperatorSymbolSummary[];
  market_signal: {
    market_context_summary: Record<string, unknown>;
    performance_windows: PerformanceWindow[];
    hold_blocked_summary: {
      hold_top_conditions: PerformanceEntry[];
      latest_blocked_reasons: string[];
      auto_resume_blockers: string[];
    };
    adaptive_signal_summary: Record<string, unknown>;
  };
  execution_windows: ExecutionWindow[];
  audit_events: AuditEvent[];
};

const decisionLabelMap: Record<string, string> = {
  hold: "보류",
  long: "롱",
  short: "숏",
  reduce: "축소",
  exit: "청산",
};

const operatingStateLabelMap: Record<string, string> = {
  TRADABLE: "신규 진입 가능",
  PROTECTION_REQUIRED: "보호 주문 확인 우선",
  DEGRADED_MANAGE_ONLY: "신규 진입 보류",
  EMERGENCY_EXIT: "비상 청산 중",
  PAUSED: "운영 일시 중지",
};

const reasonCodeLabelMap: Record<string, string> = {
  TRADING_PAUSED: "운영 중지 상태",
  HOLD_DECISION: "보류 판단",
  LIVE_APPROVAL_REQUIRED: "실거래 승인 필요",
  LIVE_TRADING_DISABLED: "실거래 비활성화",
  PROTECTION_REQUIRED: "보호 주문 복구 필요",
  DEGRADED_MANAGE_ONLY: "신규 진입 보류 상태",
  EMERGENCY_EXIT: "비상 청산 상태",
  MANUAL_USER_REQUEST: "수동 중지",
  PROTECTIVE_ORDER_FAILURE: "보호 주문 이상",
  ACCOUNT_STATE_STALE: "계좌 정보가 늦게 들어오고 있습니다.",
  POSITION_STATE_STALE: "포지션 정보가 늦게 들어오고 있습니다.",
  OPEN_ORDERS_STATE_STALE: "열린 주문 정보가 늦게 들어오고 있습니다.",
  PROTECTION_STATE_UNVERIFIED: "보호 주문 검증 불가",
};

const syncScopeLabelMap: Record<string, string> = {
  account: "계좌",
  positions: "포지션",
  open_orders: "열린 주문",
  protective_orders: "보호 주문",
  market_snapshot: "시장 스냅샷",
  market_snapshot_incomplete: "시장 정보 일부 누락",
  feature_input_missing: "분석 입력 없음",
};

const accountSummaryBasisLabelMap: Record<string, string> = {
  live_account_snapshot_preferred: "실계좌 정보 기준",
  live_account_snapshot_unavailable: "실계좌 정보 대기",
};

const accountSyncStatusLabelMap: Record<string, string> = {
  exchange_synced: "거래소 기준 동기화 완료",
  fallback_reconciled: "보수적으로 맞춰 반영",
  stale: "조금 늦음",
  unknown: "확인 전",
};

const schedulerStatusLabelMap: Record<string, string> = {
  running: "실행 중",
  success: "정상 완료",
  failed: "실패",
};

function formatNumber(value: number, digits = 0) {
  return value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatMoney(value: number) {
  return `${value > 0 ? "+" : ""}${formatNumber(value, 2)}`;
}

function formatRatio(value: number) {
  return `${formatNumber(value * 100, 2)}%`;
}

function formatYesNo(value: boolean | null | undefined) {
  if (value === null || value === undefined) {
    return "미확인";
  }
  return value ? "예" : "아니오";
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

function formatFreshnessSeconds(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  if (value < 60) {
    return `${Math.floor(value)}초`;
  }
  if (value < 3600) {
    return `${Math.floor(value / 60)}분`;
  }
  return `${Math.floor(value / 3600)}시간 ${Math.floor((value % 3600) / 60)}분`;
}

function recordString(
  source: Record<string, unknown> | null | undefined,
  key: string,
): string | null {
  const value = source?.[key];
  if (typeof value !== "string") {
    return null;
  }
  const normalized = value.trim();
  return normalized.length > 0 ? normalized : null;
}

function recordBoolean(
  source: Record<string, unknown> | null | undefined,
  key: string,
): boolean | null {
  const value = source?.[key];
  return typeof value === "boolean" ? value : null;
}

function badgeClass(kind: "good" | "warn" | "danger" | "neutral") {
  return {
    good: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warn: "border-amber-200 bg-amber-50 text-amber-800",
    danger: "border-rose-200 bg-rose-50 text-rose-800",
    neutral: "border-slate-200 bg-slate-50 text-slate-700",
  }[kind];
}

function translateDecision(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return decisionLabelMap[value] ?? value;
}

function isEntryDecision(value: string | null | undefined) {
  return value === "long" || value === "short";
}

function isSurvivalDecision(value: string | null | undefined) {
  return value === "reduce" || value === "exit";
}

function recommendationSummary(decision: string | null | undefined) {
  if (isEntryDecision(decision)) {
    return {
      label: "신규 진입 의견",
      detail: translateDecision(decision),
    };
  }
  if (isSurvivalDecision(decision)) {
    return {
      label: "정리/축소 의견",
      detail: translateDecision(decision),
    };
  }
  if (decision === "hold") {
    return {
      label: "보류 의견",
      detail: "지금은 신규 진입 권장 없음",
    };
  }
  return {
    label: "의견 없음",
    detail: "-",
  };
}

function riskOutcomeSummary(symbol: OperatorSymbolSummary) {
  const decision = symbol.risk_guard.decision ?? symbol.ai_decision.decision;
  const adjustmentReasons = filteredAdjustmentReasons(symbol);
  if (symbol.risk_guard.allowed === null) {
    return {
      label: "판단 대기",
      detail: "아직 신규 진입 판단이 집계되지 않았습니다.",
      kind: "neutral" as const,
    };
  }
  if (symbol.risk_guard.allowed) {
    if (isSurvivalDecision(decision)) {
      return {
        label: "정리/축소 허용",
        detail: translateDecision(decision),
        kind: "good" as const,
      };
    }
    if (isEntryDecision(decision)) {
      if (symbol.risk_guard.auto_resized_entry && adjustmentReasons.length > 0) {
        return {
          label: "신규 진입 가능(크기 조정)",
          detail: translateDecision(decision),
          kind: "good" as const,
        };
      }
      return {
        label: "신규 진입 가능",
        detail: translateDecision(decision),
        kind: "good" as const,
      };
    }
    return {
      label: "보류 유지",
      detail: translateDecision(decision),
      kind: "neutral" as const,
    };
  }
  if (isSurvivalDecision(decision)) {
    return {
      label: "정리/축소도 보류",
      detail: translateDecision(decision),
      kind: "danger" as const,
    };
  }
  return {
    label: "신규 진입 차단",
    detail: translateDecision(decision),
    kind: "danger" as const,
  };
}

function executionOutcomeSummary(symbol: OperatorSymbolSummary) {
  const decision = symbol.risk_guard.decision ?? symbol.ai_decision.decision;
  const executionStatus = symbol.execution.execution_status ?? symbol.execution.order_status;
  const flowLabel = isSurvivalDecision(decision)
    ? "정리/축소"
    : isEntryDecision(decision)
      ? "신규 진입"
      : "주문";

  if (!symbol.execution.order_id) {
    if (symbol.risk_guard.allowed === false) {
      return {
        label: "실행 없음",
        detail: "안전 점검에서 막혀 주문이 나가지 않았습니다.",
        kind: "danger" as const,
      };
    }
    if (symbol.ai_decision.decision === "hold") {
      return {
        label: "실행 없음",
        detail: "AI가 신규 진입을 권하지 않았습니다.",
        kind: "neutral" as const,
      };
    }
    return {
      label: "주문 없음",
      detail: "아직 주문 기록이 없습니다.",
      kind: "neutral" as const,
    };
  }

  if (symbol.execution.order_status === "filled" || symbol.execution.execution_status === "filled") {
    return {
      label: `${flowLabel} 실행 완료`,
      detail: executionStatus ?? "filled",
      kind: "good" as const,
    };
  }

  return {
    label: `${flowLabel} 주문 접수`,
    detail: executionStatus ?? "pending",
    kind: "warn" as const,
  };
}

function translateAiTriggerReason(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    entry_candidate_event: "신규 진입 후보가 생겨 확인했습니다.",
    breakout_exception_event: "강한 돌파 예외 상황을 다시 봤습니다.",
    open_position_recheck_due: "열린 포지션을 다시 확인할 시점입니다.",
    protection_review_event: "보호 주문 상태를 다시 확인했습니다.",
    manual_review_event: "운영자 요청으로 다시 확인했습니다.",
    periodic_backstop_due: "주기 점검 시간이 되어 다시 확인했습니다.",
  };
  return labels[value] ?? value;
}

function translateAiSkipReason(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    NO_EVENT: "지금은 다시 확인할 이벤트가 없습니다.",
    TRIGGER_DEDUPED: "방금 본 상태와 같아 다시 부르지 않았습니다.",
    AI_DISABLED: "AI 사용이 꺼져 있습니다.",
    AI_FAILURE_BACKOFF: "직전 오류 뒤 잠시 쉬는 중입니다.",
    AI_COOLDOWN_ACTIVE: "너무 자주 호출하지 않도록 잠시 기다리는 중입니다.",
    PROTECTION_REVIEW_DETERMINISTIC_ONLY: "보호 주문 점검은 자동 안전 규칙만 사용합니다.",
  };
  return labels[value] ?? value;
}

function aiReviewSummary(symbol: OperatorSymbolSummary) {
  if (symbol.ai_decision.last_ai_skip_reason === "NO_EVENT") {
    return {
      label: "AI 의견 없음",
      detail: "지금은 다시 확인할 이벤트가 없습니다.",
      kind: "neutral" as const,
    };
  }
  if (symbol.ai_decision.trigger_deduped || symbol.ai_decision.last_ai_skip_reason === "TRIGGER_DEDUPED") {
    return {
      label: "AI 재검토 생략",
      detail: "같은 상태라 다시 부르지 않았습니다.",
      kind: "warn" as const,
    };
  }
  if (symbol.ai_decision.last_ai_skip_reason) {
    return {
      label: "AI 미호출",
      detail: translateAiSkipReason(symbol.ai_decision.last_ai_skip_reason),
      kind: "warn" as const,
    };
  }
  if (symbol.ai_decision.last_ai_invoked_at || symbol.ai_decision.provider_name) {
    return {
      label: "AI 검토 완료",
      detail: translateAiTriggerReason(symbol.ai_decision.last_ai_trigger_reason),
      kind: "good" as const,
    };
  }
  return {
    label: "AI 상태 확인 중",
    detail: "아직 최신 AI 의견이 정리되지 않았습니다.",
    kind: "neutral" as const,
  };
}

function translateOperatingState(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return operatingStateLabelMap[value] ?? value;
}

function translateAccountSummaryBasis(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return accountSummaryBasisLabelMap[value] ?? value;
}

function translateAccountSyncStatus(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return accountSyncStatusLabelMap[value] ?? value;
}

function translateSchedulerStatus(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return schedulerStatusLabelMap[value] ?? value;
}

function translatePauseOrigin(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    manual: "운영자 수동",
    system: "시스템 자동",
    api: "API 요청",
  };
  return labels[value] ?? value;
}

function translateAutoResumeStatus(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    not_paused: "중지 상태 아님",
    idle: "복귀 대기",
    blocked: "복귀 차단",
    resumed: "복귀 완료",
    not_eligible: "대상 아님",
    waiting: "조건 대기",
    cooldown: "쿨다운 중",
  };
  return labels[value] ?? value;
}

function translateAdaptiveStatus(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    disabled: "사용 안 함",
    enabled: "사용 중",
    observe_only: "관찰만",
    unavailable: "정보 없음",
    not_applicable: "해당 없음",
    exempt: "예외",
    monitoring: "모니터링 중",
    active_disabled: "비활성 감시",
    cooldown_elapsed: "쿨다운 종료",
  };
  return labels[value] ?? value;
}

function translateProtectionRecoveryStatus(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    idle: "복구 대기",
    active: "복구 진행 중",
    recreating: "보호 주문 다시 넣는 중",
    recovery_pending: "복구 대기",
    restored: "복구 완료",
    manage_only: "신규 진입 보류",
    emergency_exit: "비상 청산 중",
  };
  return labels[value] ?? value;
}

function translateProtectionVerificationStatus(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    verified: "확인 완료",
    verify_failed: "확인 실패",
    pending: "확인 대기",
  };
  return labels[value] ?? value;
}

function translateReasonCode(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const extraReasonCodeLabelMap: Record<string, string> = {
    ENTRY_AUTO_RESIZED: "신규 진입 크기를 자동으로 줄였습니다.",
    ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT: "전체 노출 한도에 맞춰 신규 진입 크기를 줄였습니다.",
    ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT: "한쪽 방향 쏠림을 줄이기 위해 신규 진입 크기를 줄였습니다.",
    ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT: "단일 포지션 한도에 맞춰 신규 진입 크기를 줄였습니다.",
    ENTRY_CLAMPED_TO_SAME_TIER_LIMIT: "같은 티어 집중도를 낮추기 위해 신규 진입 크기를 줄였습니다.",
    ENTRY_SIZE_BELOW_MIN_NOTIONAL: "거래소 최소 주문 금액보다 작습니다.",
    ENTRY_TRIGGER_NOT_MET: "지금은 진입 조건이 아직 맞지 않습니다.",
    CHASE_LIMIT_EXCEEDED: "추격 진입 한도를 넘었습니다.",
    INVALID_INVALIDATION_PRICE: "무효화 가격 기준이 맞지 않습니다.",
  };
  return extraReasonCodeLabelMap[value] ?? lookupRiskReasonCode(value) ?? reasonCodeLabelMap[value] ?? value;
}

function translateAutoResizeReason(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return {
    CLAMPED_TO_GROSS_EXPOSURE_HEADROOM: "총 노출도 여유 범위에 맞춰 축소",
    CLAMPED_TO_DIRECTIONAL_HEADROOM: "방향 편중 여유 범위에 맞춰 축소",
    CLAMPED_TO_SINGLE_POSITION_HEADROOM: "단일 포지션 여유 범위에 맞춰 축소",
    CLAMPED_TO_SAME_TIER_HEADROOM: "동일 티어 여유 범위에 맞춰 축소",
  }[value] ?? value;
}

function translateSeverity(value: string | null | undefined) {
  if (value === "warning") {
    return "경고";
  }
  if (value === "error") {
    return "오류";
  }
  if (value === "info") {
    return "정보";
  }
  return value ?? "-";
}

function approvalWindowValue(summary: ControlStatusSummary) {
  if (summary.approval_state === "armed") {
    return "열림";
  }
  if (summary.approval_state === "grace") {
    return "유예 중";
  }
  if (summary.approval_state === "required") {
    return "승인 필요";
  }
  if (summary.approval_state === "policy_disabled") {
    return "사용 안 함";
  }
  return summary.approval_window_open ? "열림" : "닫힘";
}

function approvalWindowHint(control: OperatorDashboardPayload["control"], summary: ControlStatusSummary) {
  const approvalGraceUntil = typeof summary.approval_detail?.approval_grace_until === "string"
    ? summary.approval_detail.approval_grace_until
    : null;
  if (summary.approval_state === "armed") {
    return control.approval_expires_at
      ? `승인 만료 ${formatDateTime(control.approval_expires_at)}`
      : "지금은 실거래 승인 창이 열려 있습니다.";
  }
  if (summary.approval_state === "grace") {
    return approvalGraceUntil
      ? `자동 복귀 유예 ${formatDateTime(approvalGraceUntil)}`
      : "자동 복귀 직후라 잠시 승인 유예가 열려 있습니다.";
  }
  if (summary.approval_state === "policy_disabled") {
    return "수동 승인 절차를 사용하지 않는 설정입니다.";
  }
  return "신규 진입 전에 실거래 승인 창을 다시 열어야 합니다.";
}

function formatAuditValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "boolean") {
    return value ? "예" : "아니오";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : formatNumber(value, 4);
  }
  if (Array.isArray(value)) {
    return value.map((item) => formatAuditValue(item)).join(", ");
  }
  if (typeof value === "string") {
    if (value.includes("T") && !Number.isNaN(Date.parse(value))) {
      return formatDateTime(value);
    }
    return value;
  }
  return JSON.stringify(value);
}

function auditPayloadRows(payload: Record<string, unknown> | null | undefined): Array<[string, string]> {
  if (!payload) {
    return [];
  }
  const rows: Array<[string, string]> = [];
  for (const [key, value] of Object.entries(payload)) {
    if (value === null || value === undefined) {
      continue;
    }
    if (typeof value === "object" && !Array.isArray(value)) {
      for (const [nestedKey, nestedValue] of Object.entries(value)) {
        rows.push([`${key}.${nestedKey}`, formatAuditValue(nestedValue)]);
      }
      continue;
    }
    rows.push([key, formatAuditValue(value)]);
  }
  return rows;
}

function translateSyncScope(value: string) {
  return syncScopeLabelMap[value] ?? value;
}

function formatMarketTiming(symbol: OperatorSymbolSummary) {
  const parts: string[] = [];
  if (symbol.market_candle_time) {
    parts.push(`캔들 ${formatDateTime(symbol.market_candle_time)}`);
  }
  if (symbol.market_snapshot_time) {
    parts.push(`수집 ${formatDateTime(symbol.market_snapshot_time)}`);
  }
  return parts.join(" / ") || "기록 없음";
}

function syncBadge(scope: SyncScopeStatus | undefined) {
  if (!scope) {
    return { label: "미확인", kind: "warn" as const };
  }
  if (scope.incomplete) {
    return { label: "불완전", kind: "danger" as const };
  }
  if (scope.stale) {
    return { label: "조금 늦음", kind: "warn" as const };
  }
  return { label: "정상", kind: "good" as const };
}

function valueCard(title: string, value: string, hint: string) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
      <p className="text-xs font-medium text-slate-500">{title}</p>
      <p className="mt-2 text-xl font-semibold text-slate-950">{value}</p>
      <p className="mt-2 text-xs leading-5 text-slate-500">{hint}</p>
    </div>
  );
}

function translateProtectionStatus(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    protected: "정상",
    missing: "확인 필요",
    unprotected: "미보호",
    pending: "확인 대기",
    active: "복구 진행 중",
    recovery_pending: "복구 대기",
    recreating: "다시 넣는 중",
    manage_only: "신규 진입 보류",
    emergency_exit: "비상 청산 중",
    failed: "오류",
  };
  return labels[value] ?? value;
}

function detailSectionCard(section: ReturnType<typeof buildOperatorDetailSections>[number]) {
  return (
    <div key={section.key} className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(section.tone as OperatorDetailTone)}`}>
          {section.title}
        </span>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {section.items.map((item) =>
          valueCard(item.label, item.value, item.hint),
        )}
      </div>
      {section.alerts.length > 0 ? (
        <div className="mt-4 space-y-2">
          {section.alerts.map((alert, index) => (
            <div
              key={`${section.key}-alert-${index}`}
              className={`rounded-2xl px-4 py-3 text-sm ${
                alert.tone === "danger"
                  ? "bg-rose-50 text-rose-800"
                  : alert.tone === "warn"
                    ? "bg-amber-50 text-amber-900"
                    : alert.tone === "good"
                      ? "bg-emerald-50 text-emerald-800"
                      : "bg-slate-50 text-slate-600"
              }`}
            >
              {alert.text}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function dedupeReasons(values: string[]) {
  return values.filter((item, index, array) => array.indexOf(item) === index);
}

function rolloutModeLabel(mode: RolloutMode) {
  switch (mode) {
    case "paper":
      return "모의 운영";
    case "shadow":
      return "그림자 점검";
    case "live_dry_run":
      return "실거래 사전 점검";
    case "limited_live":
      return "제한된 실거래";
    case "full_live":
      return "실거래 전체 허용";
    default:
      return mode;
  }
}

function resolveControlStatusSummary(control: OperatorDashboardPayload["control"]): ControlStatusSummary {
  const summary = control.control_status_summary;
  return {
    exchange_can_trade: summary?.exchange_can_trade ?? null,
    rollout_mode: summary?.rollout_mode ?? control.rollout_mode,
    exchange_submit_allowed: summary?.exchange_submit_allowed ?? control.exchange_submit_allowed,
    limited_live_max_notional: summary?.limited_live_max_notional ?? control.limited_live_max_notional,
    app_live_armed: summary?.app_live_armed ?? control.approval_armed,
    approval_window_open: summary?.approval_window_open ?? control.approval_armed,
    approval_state: summary?.approval_state ?? null,
    approval_detail: summary?.approval_detail ?? {},
    paused: summary?.paused ?? control.trading_paused,
    degraded: summary?.degraded ?? control.operating_state === "DEGRADED_MANAGE_ONLY",
    risk_allowed: summary?.risk_allowed ?? null,
    blocked_reasons_current_cycle: dedupeReasons(
      summary?.blocked_reasons_current_cycle ?? control.latest_blocked_reasons,
    ),
  };
}

function controlGateCards(control: OperatorDashboardPayload["control"]) {
  const summary = resolveControlStatusSummary(control);
  const primaryBlocker = summary.blocked_reasons_current_cycle[0];
  return [
    {
      title: "실거래 적용 단계",
      value: rolloutModeLabel(summary.rollout_mode),
      hint:
        summary.rollout_mode === "paper"
          ? "시장 수집과 판단만 하고 실제 주문은 보내지 않습니다."
          : summary.rollout_mode === "shadow"
            ? "실주문 없이 판단과 기록만 함께 점검합니다."
            : summary.rollout_mode === "live_dry_run"
            ? "실거래 전 점검까지는 하지만 실제 주문 전송은 막습니다."
              : summary.rollout_mode === "limited_live"
                ? `실주문은 허용하지만 주문당 금액을 ${summary.limited_live_max_notional ?? 0} USDT 이하로 제한합니다.`
                : "실거래 경로를 그대로 사용합니다.",
      kind:
        summary.rollout_mode === "full_live"
          ? ("good" as const)
          : summary.rollout_mode === "limited_live"
            ? ("warn" as const)
            : ("neutral" as const),
    },
    {
      title: "거래소 주문 가능 상태",
      value:
        summary.exchange_can_trade === null
          ? "미확인"
          : summary.exchange_can_trade
            ? "가능"
            : "차단",
      hint:
        summary.exchange_can_trade === null
          ? "최근 계좌 동기화에서 거래소 주문 가능 여부를 아직 확인하지 못했습니다."
          : summary.exchange_can_trade
            ? "거래소 계좌 상태 기준으로 새 주문을 보낼 수 있습니다."
            : "거래소 계좌 상태 기준으로 새 주문이 막혀 있습니다.",
      kind:
        summary.exchange_can_trade === null
          ? ("neutral" as const)
          : summary.exchange_can_trade
            ? ("good" as const)
            : ("danger" as const),
    },
    {
      title: "앱 실거래 준비",
      value: summary.app_live_armed ? "준비됨" : "해제됨",
      hint: summary.app_live_armed
        ? "앱 쪽 실거래 제출 경로가 열려 있습니다."
        : "앱 쪽 실거래 제출 경로가 내려가 있어 주문이 나가지 않습니다.",
      kind: summary.app_live_armed ? ("good" as const) : ("warn" as const),
    },
    {
      title: "실거래 승인 상태",
      value: approvalWindowValue(summary),
      hint: approvalWindowHint(control, summary),
      kind: summary.approval_window_open ? ("good" as const) : ("warn" as const),
    },
    {
      title: "운영 일시 중지",
      value: summary.paused ? "중지됨" : "운영 중",
      hint: summary.paused
        ? translateReasonCode(control.pause_reason_code)
        : "운영 중지 설정이 걸려 있지 않습니다.",
      kind: summary.paused ? ("danger" as const) : ("good" as const),
    },
    {
      title: "안전 모드",
      value: summary.degraded ? "신규 진입 보류" : "정상",
      hint: summary.degraded
        ? `${translateOperatingState(control.operating_state)} / 보호 복구 ${translateProtectionRecoveryStatus(control.protection_recovery_status)}`
        : "보호 주문 복구나 비상 대응으로 내려간 상태는 아닙니다.",
      kind: summary.degraded ? ("warn" as const) : ("good" as const),
    },
    {
      title: "신규 진입 판단",
      value:
        summary.risk_allowed === null
          ? "판단 전"
          : summary.risk_allowed
            ? "허용"
            : "차단",
      hint:
        summary.risk_allowed === null
          ? "이번 판단 주기의 신규 진입 결과가 아직 집계되지 않았습니다."
          : summary.risk_allowed
            ? "이번 판단 주기 기준으로 신규 진입이 가능합니다."
            : primaryBlocker
              ? translateReasonCode(primaryBlocker)
              : control.guard_mode_reason_message ?? "이번 판단 주기 기준으로 신규 진입이 막혀 있습니다.",
      kind:
        summary.risk_allowed === null
          ? ("neutral" as const)
          : summary.risk_allowed
            ? ("good" as const)
            : ("danger" as const),
    },
  ];
}

async function fetchPayload(): Promise<OperatorDashboardPayload> {
  const response = await fetch(`${apiBaseUrl}/api/dashboard/operator`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || response.statusText);
  }
  return (await response.json()) as OperatorDashboardPayload;
}

function filteredBlockedReasons(symbol: OperatorSymbolSummary) {
  const source =
    symbol.risk_guard.blocked_reason_codes.length > 0
      ? symbol.risk_guard.blocked_reason_codes
      : symbol.blocked_reasons;
  return source.filter((item, index, array) => array.indexOf(item) === index);
}

function filteredAdjustmentReasons(symbol: OperatorSymbolSummary) {
  return symbol.risk_guard.adjustment_reason_codes.filter((item, index, array) => array.indexOf(item) === index);
}

function accountSnapshotCard(control: OperatorDashboardPayload["control"]) {
  const accountSummary = control.account_sync_summary;
  const pnlSummary = control.pnl_summary;
  const snapshotAvailable = recordBoolean(accountSummary, "account_snapshot_available");
  const lastSyncedAt = recordString(accountSummary, "last_synced_at");
  const note =
    recordString(accountSummary, "note") ??
    recordString(pnlSummary, "basis_note") ??
    "첫 계좌 동기화 전까지는 자산과 잔고를 확정하지 않습니다.";
  const syncStatus = translateAccountSyncStatus(recordString(accountSummary, "status"));
  const basis = translateAccountSummaryBasis(recordString(pnlSummary, "basis"));

  if (snapshotAvailable) {
    const rawStatus = recordString(accountSummary, "status");
    const value =
      rawStatus === "stale"
        ? "조금 늦은 계좌 정보"
        : rawStatus === "fallback_reconciled"
          ? "보수적으로 맞춘 계좌 정보"
          : "실계좌 반영";
    return {
      value,
      hint: `${syncStatus} / ${lastSyncedAt ? `마지막 동기화 ${formatDateTime(lastSyncedAt)}` : basis}`,
    };
  }

  return {
    value: "계좌 정보 대기",
    hint: `${basis} / ${note}`,
  };
}

function latestActivityTimestamp(symbol: OperatorSymbolSummary) {
  const value =
    symbol.last_updated_at ??
    symbol.execution.execution_created_at ??
    symbol.execution.created_at ??
    symbol.risk_guard.as_of ??
    symbol.risk_guard.created_at ??
    symbol.ai_decision.created_at;
  if (!value) {
    return 0;
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function resolveFocusSymbolSummary(
  symbols: OperatorSymbolSummary[],
  selectedSymbol: string,
  defaultSymbol: string,
) {
  if (selectedSymbol !== ALL_SYMBOLS) {
    return symbols.find((item) => item.symbol === selectedSymbol) ?? null;
  }
  const defaultMatch = symbols.find((item) => item.symbol === defaultSymbol);
  return [...symbols].sort((left, right) => latestActivityTimestamp(right) - latestActivityTimestamp(left))[0] ?? defaultMatch ?? null;
}

function performanceRows(title: string, rows: PerformanceEntry[], empty: string) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-950">{title}</h3>
      <div className="mt-3 space-y-3">
        {rows.length === 0 ? (
          <p className="text-sm text-slate-500">{empty}</p>
        ) : (
          rows.map((row) => (
            <div key={`${title}-${row.key}`} className="rounded-2xl bg-slate-50 px-3 py-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium text-slate-950">{row.key}</p>
                <span className="text-sm font-semibold text-slate-900">
                  {formatMoney(row.net_realized_pnl_total)}
                </span>
              </div>
              <p className="mt-2 text-xs text-slate-500">
                승 {row.wins} / 패 {row.losses} / 보류 {row.holds} / 평균 슬리피지{" "}
                {formatRatio(row.average_slippage_pct)}
              </p>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function GlobalOperatorSummary({
  control,
  market,
  execution24h,
  selectedSymbol,
  focusSymbol,
  globalAuditEvents,
}: {
  control: OperatorDashboardPayload["control"];
  market: OperatorDashboardPayload["market_signal"];
  execution24h: ExecutionWindow | undefined;
  selectedSymbol: string;
  focusSymbol: OperatorSymbolSummary | null;
  globalAuditEvents: AuditEvent[];
}) {
  const controlSummary = resolveControlStatusSummary(control);
  const currentCycleBlockedReasons = controlSummary.blocked_reasons_current_cycle;
  const gateCards = controlGateCards(control);
  const status = controlSummary.paused
    ? {
        kind: "danger" as const,
        label: "운영 중지",
        detail: translateReasonCode(control.pause_reason_code),
      }
    : controlSummary.degraded
      ? {
          kind: "warn" as const,
          label: "신규 진입 보류",
          detail: `${translateOperatingState(control.operating_state)} 상태라 보호 주문 확인과 복구를 우선합니다.`,
        }
      : controlSummary.exchange_can_trade === false
        ? {
            kind: "danger" as const,
            label: "거래소 주문 차단",
            detail: "거래소 계좌 상태상 지금은 새 주문을 보낼 수 없습니다.",
          }
        : controlSummary.risk_allowed === false
          ? {
              kind: "warn" as const,
              label: "신규 진입 차단",
              detail: currentCycleBlockedReasons[0]
                ? translateReasonCode(currentCycleBlockedReasons[0])
                : control.guard_mode_reason_message ?? "이번 판단 주기 기준으로 신규 진입이 막혀 있습니다.",
            }
          : control.live_execution_ready
            ? {
                kind: "good" as const,
                label: "신규 진입 가능",
                detail: "계좌 상태, 승인, 동기화, 안전 점검이 현재 기준을 모두 통과했습니다.",
              }
            : {
                kind: "warn" as const,
                label: "주의 상태",
                detail: control.guard_mode_reason_message ?? "아직 신규 진입 조건이 모두 갖춰지지 않았습니다.",
              };
  const primaryWindow = market.performance_windows[0];
  const syncScopes = [
    ["account", "계좌"],
    ["positions", "포지션"],
    ["open_orders", "오더"],
    ["protective_orders", "보호 주문"],
  ] as const;
  const focusRecommendation = focusSymbol ? recommendationSummary(focusSymbol.ai_decision.decision) : null;
  const focusRiskOutcome = focusSymbol ? riskOutcomeSummary(focusSymbol) : null;
  const focusExecutionOutcome = focusSymbol ? executionOutcomeSummary(focusSymbol) : null;
  const recentGlobalAuditEvents = globalAuditEvents.slice(0, 3);
  const accountSnapshot = accountSnapshotCard(control);

  return (
    <section className="space-y-6 rounded-[2rem] border border-amber-200/70 bg-white/90 p-6 shadow-frame sm:p-7">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.34em] text-slate-500">
            전역 운영 요약
          </p>
          <h1 className="mt-3 font-display text-3xl leading-tight text-slate-950 sm:text-4xl">
            전역 운영 상태와 심볼별 현황
          </h1>
          <p className="mt-4 max-w-3xl text-sm leading-7 text-slate-600 sm:text-base">
            이 화면이 운영 상태의 단일 기준 화면입니다. 상단은 계좌와 시스템 전역 상태만 보여주고,
            그 아래에서 심볼별 AI 의견, 신규 진입 판단, 주문 상태를 분리해서 확인합니다.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(status.kind)}`}>
            {status.label}
          </span>
          <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass("neutral")}`}>
            추적 심볼 {control.tracked_symbol_count}개
          </span>
          <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass("neutral")}`}>
            현재 선택 {selectedSymbol === ALL_SYMBOLS ? "전체" : selectedSymbol}
          </span>
        </div>
      </div>

      <div className="rounded-[1.75rem] bg-slate-950 p-5 text-white">
        <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-white/60">
          지금 가장 직접적인 운영 상태
        </p>
        <p className="mt-2 text-base leading-7 sm:text-lg">{status.detail}</p>
        <div className="mt-4 flex flex-wrap gap-3 text-sm text-white/75">
          <span>운영 상태 {translateOperatingState(control.operating_state)}</span>
          <span>자동 복구 {translateAutoResumeStatus(control.auto_resume_status)}</span>
          <span>보호 복구 {translateProtectionRecoveryStatus(control.protection_recovery_status)}</span>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {valueCard(
          "신규 진입",
          control.can_enter_new_position ? "가능" : "차단",
          control.guard_mode_reason_message ?? "전역 운영 상태 기준으로 계산합니다.",
        )}
        {valueCard(
          "실거래 승인",
          approvalWindowValue(controlSummary),
          approvalWindowHint(control, controlSummary),
        )}
        {valueCard(
          "열린 포지션",
          `${control.open_positions}`,
          `보호됨 ${control.protected_positions} / 미보호 ${control.unprotected_positions}`,
        )}
        {valueCard(
          "오늘 손익",
          formatMoney(control.daily_pnl),
          `누적 ${formatMoney(control.cumulative_pnl)}`,
        )}
        {valueCard("계좌 정보 반영", accountSnapshot.value, accountSnapshot.hint)}
      </div>

      {focusSymbol && focusRecommendation && focusRiskOutcome && focusExecutionOutcome ? (
        <div className="grid gap-4 lg:grid-cols-3">
          {valueCard(
            `최신 AI 의견 (${focusSymbol.symbol})`,
            focusRecommendation.label,
            focusRecommendation.detail,
          )}
          {valueCard(
            `최신 신규 진입 판단 (${focusSymbol.symbol})`,
            focusRiskOutcome.label,
            focusRiskOutcome.detail,
          )}
          {valueCard(
            `최신 주문 상태 (${focusSymbol.symbol})`,
            focusExecutionOutcome.label,
            focusExecutionOutcome.detail,
          )}
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-3">
        {gateCards.map((card) => (
          <div key={card.title} className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-medium text-slate-500">{card.title}</p>
              <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(card.kind)}`}>
                {card.value}
              </span>
            </div>
            <p className="mt-3 text-sm leading-6 text-slate-700">{card.hint}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-2xl border border-slate-200 bg-white">
          {[
            ["기본 심볼 / 시장 타임프레임", `${control.default_symbol} / ${control.default_timeframe}`],
            ["추적 심볼", control.tracked_symbols.join(", ")],
            ["중지 이유", translateReasonCode(control.pause_reason_code)],
            ["중지 주체", translatePauseOrigin(control.pause_origin)],
            ["자동 복귀 가능 여부", control.auto_resume_eligible ? "가능" : "불가"],
            ["자동 복귀 예정 시각", formatDateTime(control.auto_resume_after)],
            [
              "자동 복귀가 막힌 이유",
              control.auto_resume_last_blockers.length > 0
                ? control.auto_resume_last_blockers.map(translateReasonCode).join(", ")
                : "-",
            ],
            [
              "자동 점검 주기",
              control.scheduler_window || control.scheduler_status
                ? `${control.scheduler_window ?? "-"} / ${translateSchedulerStatus(control.scheduler_status)}`
                : "-",
            ],
            ["다음 자동 점검 예정", formatDateTime(control.scheduler_next_run_at)],
            [
              "계좌 정보 반영",
              recordBoolean(control.account_sync_summary, "account_snapshot_available") ? "실계좌 반영" : "없음",
            ],
            ["계좌 동기화 상태", translateAccountSyncStatus(recordString(control.account_sync_summary, "status"))],
            ["계좌 요약 기준", translateAccountSummaryBasis(recordString(control.pnl_summary, "basis"))],
          ].map(([label, value], index) => (
            <div
              key={String(label)}
              className={`flex flex-col gap-1 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between ${
                index === 0 ? "" : "border-t border-slate-100"
              }`}
            >
              <span className="text-slate-500">{label}</span>
              <span className="font-medium text-slate-900">{value}</span>
            </div>
          ))}
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-950">지금 신규 진입이 막힌 이유</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            이번 판단 주기와 전역 제어 상태 기준으로 실제로 신규 진입을 막는 이유만 보여줍니다.
          </p>
          <div className="mt-4 space-y-2">
            {currentCycleBlockedReasons.map((reason) => (
                <div key={reason} className="rounded-2xl bg-amber-50 px-4 py-3 text-sm text-slate-800">
                  {translateReasonCode(reason)}
                </div>
              ))}
            {currentCycleBlockedReasons.length === 0 ? (
              <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
                이번 판단 주기 기준으로는 막힌 이유가 없습니다.
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-semibold text-slate-950">거래소 정보 최신성</h3>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          계좌, 포지션, 열린 주문, 보호 주문 중 하나라도 늦거나 불완전하면 신규 진입은 막힐 수
          있습니다.
        </p>
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {syncScopes.map(([key, label]) => {
            const scope = control.sync_freshness_summary[key];
            const badge = syncBadge(scope);
            return (
              <div key={key} className="rounded-2xl bg-slate-50 px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-medium text-slate-950">{label}</p>
                  <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(badge.kind)}`}>
                    {badge.label}
                  </span>
                </div>
                <div className="mt-3 space-y-1 text-xs text-slate-600">
                  <p>마지막 동기화 {formatDateTime(scope?.last_sync_at)}</p>
                  <p>지난 시간 {formatFreshnessSeconds(scope?.freshness_seconds ?? null)}</p>
                  <p>지연 기준 {formatFreshnessSeconds(scope?.stale_after_seconds ?? null)}</p>
                  <p>마지막 실패 {translateReasonCode(scope?.last_failure_reason ?? "-")}</p>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="space-y-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">
            운영 보조 요약
          </p>
          <h2 className="mt-2 text-xl font-semibold text-slate-950">최근 24시간 빠른 요약</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            가장 자주 보는 지표만 먼저 보여주고, 자세한 집계는 접힌 섹션에서 확인할 수 있습니다.
          </p>
        </div>

        <div className="grid gap-4 xl:grid-cols-3">
          {valueCard(
            "24h 순실현 손익",
            primaryWindow ? formatMoney(primaryWindow.summary.net_realized_pnl_total) : "-",
            primaryWindow ? `수수료 ${formatMoney(primaryWindow.summary.fee_total)}` : "최근 24h 집계 없음",
          )}
          {valueCard(
            "24h 승률",
            primaryWindow && primaryWindow.summary.wins + primaryWindow.summary.losses > 0
              ? formatRatio(primaryWindow.summary.wins / (primaryWindow.summary.wins + primaryWindow.summary.losses))
              : "-",
            primaryWindow
              ? `승 ${primaryWindow.summary.wins} / 패 ${primaryWindow.summary.losses} / 보류 ${primaryWindow.summary.holds}`
              : "최근 24h 판단 집계 없음",
          )}
          {valueCard(
            "24h 실행 품질",
            `${formatNumber(Number(execution24h?.execution_quality_summary.average_realized_slippage_pct ?? 0), 2)}%`,
            execution24h ? "평균 실슬리피지" : "최근 24h 실행 품질 집계 없음",
          )}
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <h3 className="text-sm font-semibold text-slate-950">최근 보류/차단 이유</h3>
            <div className="mt-4 space-y-2">
              {market.hold_blocked_summary.hold_top_conditions.length === 0 ? (
                <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
                  최근 보류가 집중된 이유는 없습니다.
                </div>
              ) : (
                market.hold_blocked_summary.hold_top_conditions.map((item) => (
                  <div key={item.key} className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-700">
                    {item.key}
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <h3 className="text-sm font-semibold text-slate-950">자동 조정 요약</h3>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {valueCard(
                "현재 상태",
                translateAdaptiveStatus(String(market.adaptive_signal_summary.status ?? "disabled")),
                `입력 ${Array.isArray(market.adaptive_signal_summary.active_inputs) ? market.adaptive_signal_summary.active_inputs.join(", ") || "없음" : "없음"}`,
              )}
              {valueCard(
                "신뢰도 / 거래 리스크",
                `${formatNumber(Number(market.adaptive_signal_summary.confidence_multiplier ?? 1), 2)}x`,
                `거래 리스크 배수 ${formatNumber(Number(market.adaptive_signal_summary.risk_pct_multiplier ?? 1), 2)}x`,
              )}
            </div>
          </div>
        </div>

        <details className="rounded-2xl border border-slate-200 bg-white p-4">
          <summary className="cursor-pointer list-none text-sm font-semibold text-slate-950">
            상세 성과 보기
          </summary>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            상위 rationale, 레짐, 심볼 성과와 최근 감사 요약은 필요할 때만 펼쳐서 봅니다.
          </p>
          <div className="mt-4 grid gap-4 xl:grid-cols-2">
            {performanceRows(
              "상위 수익 rationale",
              primaryWindow?.rationale_winners ?? [],
              "최근 구간에서 상위 수익 rationale 데이터가 없습니다.",
            )}
            {performanceRows(
              "상위 손실 rationale",
              primaryWindow?.rationale_losers ?? [],
              "최근 구간에서 상위 손실 rationale 데이터가 없습니다.",
            )}
          </div>
          <div className="mt-4 grid gap-4 xl:grid-cols-2">
            {performanceRows("레짐별 성과", primaryWindow?.top_regimes ?? [], "레짐별 집계가 없습니다.")}
            {performanceRows("심볼별 성과", primaryWindow?.top_symbols ?? [], "심볼별 집계가 없습니다.")}
          </div>
          <div className="mt-4 rounded-2xl bg-slate-50 p-4">
            <h4 className="text-sm font-semibold text-slate-950">최근 감사 요약</h4>
            <div className="mt-3 space-y-3">
              {recentGlobalAuditEvents.length === 0 ? (
                <div className="rounded-2xl bg-white px-4 py-3 text-sm text-slate-500">
                  최근 전역 감사 이벤트가 없습니다.
                </div>
              ) : (
                recentGlobalAuditEvents.map((event) => (
                  <div
                    key={`${event.event_type}-${event.entity_id}-${event.created_at}`}
                    className="rounded-2xl bg-white px-4 py-3"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                          event.severity === "error"
                            ? "danger"
                            : event.severity === "warning"
                              ? "warn"
                              : "neutral",
                        )}`}
                      >
                        {translateSeverity(event.severity)}
                      </span>
                      <span className="text-xs text-slate-500">{formatDateTime(event.created_at)}</span>
                    </div>
                    <p className="mt-2 text-sm text-slate-800">{event.message}</p>
                    {auditPayloadRows(event.payload).length > 0 ? (
                      <div className="mt-3 grid gap-2 sm:grid-cols-2">
                        {auditPayloadRows(event.payload).map(([label, value]) => (
                          <div key={`${event.event_type}-${label}`} className="rounded-xl bg-slate-50 px-3 py-2">
                            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">{label}</p>
                            <p className="mt-1 text-sm text-slate-700">{value}</p>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ))
              )}
            </div>
          </div>
        </details>
      </div>
    </section>
  );
}

function SymbolFilterBar({
  symbols,
  selectedSymbol,
  onSelect,
}: {
  symbols: string[];
  selectedSymbol: string;
  onSelect: (value: string) => void;
}) {
  return (
    <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
      <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">심볼 선택</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {[ALL_SYMBOLS, ...symbols].map((symbol) => {
          const isSelected = selectedSymbol === symbol;
          return (
            <button
              key={symbol}
              type="button"
              onClick={() => onSelect(symbol)}
              className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${
                isSelected
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50"
              }`}
            >
              {symbol === ALL_SYMBOLS ? "전체" : symbol}
            </button>
          );
        })}
      </div>
      <p className="mt-3 text-sm text-slate-600">
        전체 모드에서는 심볼별 핵심 상태를 비교하고, 개별 심볼을 선택하면 AI 의견, 신규 진입 판단,
        주문 결과, 감사 기록을 해당 심볼 기준으로만 자세히 보여줍니다.
      </p>
    </section>
  );
}

function SymbolStatusBoard({
  symbols,
  selectedSymbol,
  onSelect,
}: {
  symbols: OperatorSymbolSummary[];
  selectedSymbol: string;
  onSelect: (value: string) => void;
}) {
  const visibleSymbols = filterSymbolsBySelection(symbols, selectedSymbol);

  return (
    <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
      <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">심볼별 상태 비교</p>
      <h2 className="mt-2 text-xl font-semibold text-slate-950">심볼별 운영 상태 비교</h2>
      <p className="mt-2 text-sm leading-6 text-slate-600">
        각 행은 하나의 심볼만 보여줍니다. AI 의견, 신규 진입 판단, 주문 상태가 서로 다른 심볼과 섞이지
        않도록 최신 스냅샷을 나눠서 보여줍니다.
      </p>
      <div className="mt-5 overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-y-2">
          <thead>
            <tr className="text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
              <th className="px-3 py-2">심볼</th>
              <th className="px-3 py-2">현재가</th>
              <th className="px-3 py-2">AI 의견</th>
              <th className="px-3 py-2">신뢰도</th>
              <th className="px-3 py-2">신규 진입 판단</th>
              <th className="px-3 py-2">막힌 이유</th>
              <th className="px-3 py-2">포지션</th>
              <th className="px-3 py-2">보호 상태</th>
              <th className="px-3 py-2">주문/체결</th>
              <th className="px-3 py-2">마지막 갱신</th>
            </tr>
          </thead>
          <tbody>
            {visibleSymbols.map((item) => {
              const blockedReasons = filteredBlockedReasons(item);
              const recommendation = recommendationSummary(item.ai_decision.decision);
              const review = aiReviewSummary(item);
              const riskOutcome = riskOutcomeSummary(item);
              const executionOutcome = executionOutcomeSummary(item);
              const rowSelected = selectedSymbol !== ALL_SYMBOLS && item.symbol === selectedSymbol;
              return (
                <tr
                  key={item.symbol}
                  className={`cursor-pointer rounded-2xl bg-white shadow-sm transition hover:bg-slate-50 ${
                    rowSelected ? "outline outline-2 outline-slate-900" : ""
                  }`}
                  onClick={() => onSelect(item.symbol)}
                >
                  <td className="rounded-l-2xl px-3 py-3 text-sm font-semibold text-slate-950">{item.symbol}</td>
                  <td className="px-3 py-3 text-sm text-slate-700">
                    {item.latest_price !== null ? formatNumber(item.latest_price, 2) : "-"}
                  </td>
                  <td className="px-3 py-3 text-sm text-slate-700">
                    <div className="space-y-1">
                      <div className="font-medium text-slate-900">{recommendation.label}</div>
                      <div className="text-xs text-slate-500">{recommendation.detail}</div>
                      <div className="text-xs text-slate-500">
                        {review.label} / {review.detail}
                      </div>
                    </div>
                  </td>
                  <td className="px-3 py-3 text-sm text-slate-700">
                    {item.ai_decision.confidence !== null ? formatRatio(item.ai_decision.confidence) : "-"}
                  </td>
                  <td className="px-3 py-3 text-sm text-slate-700">
                    <div className="space-y-1">
                      <div className="font-medium text-slate-900">{riskOutcome.label}</div>
                      <div className="text-xs text-slate-500">{riskOutcome.detail}</div>
                    </div>
                  </td>
                  <td className="px-3 py-3 text-sm text-slate-700">
                    {blockedReasons.length > 0 ? blockedReasons.map(translateReasonCode).join(", ") : "-"}
                  </td>
                  <td className="px-3 py-3 text-sm text-slate-700">
                    {item.open_position.is_open
                      ? `${translateDecision(item.open_position.side ?? "-")} / ${item.open_position.quantity ?? 0}`
                      : "없음"}
                  </td>
                  <td className="px-3 py-3 text-sm text-slate-700">
                    <div className="space-y-1">
                      <div>
                        {translateProtectionStatus(item.protection_status.status)}
                        {item.protection_status.missing_components.length > 0
                          ? ` (${item.protection_status.missing_components.join(", ")})`
                          : ""}
                      </div>
                      {item.protection_status.recovery_status ? (
                        <div className="text-xs text-slate-500">
                          {translateProtectionRecoveryStatus(item.protection_status.recovery_status)}
                          {item.protection_status.auto_recovery_active ? " / 자동" : ""}
                        </div>
                      ) : null}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-sm text-slate-700">
                    <div className="space-y-1">
                      <div className="font-medium text-slate-900">{executionOutcome.label}</div>
                      <div className="text-xs text-slate-500">{executionOutcome.detail}</div>
                    </div>
                  </td>
                  <td className="rounded-r-2xl px-3 py-3 text-sm text-slate-700">
                    {formatDateTime(item.last_updated_at)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function detailList(rows: Array<[string, string]>) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white">
      {rows.map(([label, value], index) => (
        <div
          key={label}
          className={`flex flex-col gap-1 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between ${
            index === 0 ? "" : "border-t border-slate-100"
          }`}
        >
          <span className="text-slate-500">{label}</span>
          <span className="font-medium text-slate-900">{value}</span>
        </div>
      ))}
    </div>
  );
}

function SymbolDetailPanel({
  selectedSymbol,
  symbol,
}: {
  selectedSymbol: string;
  symbol: OperatorSymbolSummary | null;
}) {
  if (selectedSymbol === ALL_SYMBOLS || symbol === null) {
    return (
      <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">심볼 상세</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">심볼 상세</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          전체 모드에서는 심볼 비교만 보여줍니다. 아래 상세 흐름은 특정 심볼을 선택했을 때만
          표시됩니다.
        </p>
        <div className="mt-5 rounded-2xl border border-dashed border-slate-300 px-4 py-5 text-sm text-slate-600">
          상세 AI 의견, 신규 진입 판단, 주문 결과, 감사 기록을 보려면 심볼을 선택하세요.
        </div>
        <div className="mt-5 rounded-2xl border border-slate-200 bg-white p-4 text-sm leading-6 text-slate-600">
          최근 전역 감사 이벤트와 성과 세부는 상단 summary의 접힌 섹션에서 확인할 수 있습니다.
          심볼을 선택하면 해당 심볼 기준의 AI 의견, 신규 진입 허용/차단, 실제 주문 흐름이 아래에 표시됩니다.
        </div>
      </section>
    );
  }

  const blockedReasons = filteredBlockedReasons(symbol);
  const adjustmentReasons = filteredAdjustmentReasons(symbol);
  const autoResized = symbol.risk_guard.auto_resized_entry;
  const recommendation = recommendationSummary(symbol.ai_decision.decision);
  const review = aiReviewSummary(symbol);
  const riskOutcome = riskOutcomeSummary(symbol);
  const executionOutcome = executionOutcomeSummary(symbol);
  const detailSections = buildOperatorDetailSections(symbol);

  return (
    <section className="space-y-6 rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">심볼 상세</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">{symbol.symbol} 상세 운영 흐름</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          아래 정보는 선택한 심볼의 최신 AI 의견, 신규 진입 판단, 주문 상태, 감사 기록만
          보여줍니다. 막힌 이유는 AI 설명이 아니라 안전 점검 결과 기준으로 해석합니다.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-4">
        {valueCard(
          "현재가",
          symbol.latest_price !== null ? formatNumber(symbol.latest_price, 2) : "-",
          formatMarketTiming(symbol),
        )}
        {valueCard(
          "데이터 상태",
          symbol.stale_flags.length === 0 ? "정상" : "주의",
          symbol.stale_flags.length === 0
            ? "계좌, 포지션, 주문, 시장 정보가 모두 정상 범위입니다."
            : symbol.stale_flags.map(translateSyncScope).join(", "),
        )}
        {valueCard(
          "포지션",
          symbol.open_position.is_open ? `${symbol.open_position.side ?? "-"} / ${symbol.open_position.quantity ?? 0}` : "없음",
          symbol.open_position.is_open
            ? `진입가 ${symbol.open_position.entry_price ?? "-"} / 평가 ${symbol.open_position.mark_price ?? "-"}`
            : "현재 열린 포지션이 없습니다.",
        )}
        {valueCard(
          "보호 상태",
          translateProtectionStatus(symbol.protection_status.status),
          symbol.protection_status.last_error
            ? symbol.protection_status.last_error
            : symbol.protection_status.missing_components.length > 0
              ? `누락 ${symbol.protection_status.missing_components.join(", ")}`
              : "보호 주문 구성 정상",
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        {detailSections.map((section) => detailSectionCard(section))}
      </div>

      <div className="grid gap-4 lg:grid-cols-4">
        {valueCard("AI 상태", review.label, review.detail)}
        {valueCard(
          "AI 호출 또는 생략 이유",
          translateAiTriggerReason(symbol.ai_decision.last_ai_trigger_reason),
          `미호출 ${translateAiSkipReason(symbol.ai_decision.last_ai_skip_reason)}`,
        )}
        {valueCard(
          "최근 / 다음 확인",
          formatDateTime(symbol.ai_decision.last_ai_invoked_at),
          `다음 ${formatDateTime(symbol.ai_decision.next_ai_review_due_at)}`,
        )}
        {valueCard(
          "배정 슬롯 / 우선순위",
          symbol.ai_decision.assigned_slot ?? symbol.candidate_selection.assigned_slot ?? "-",
          `가중치 ${
            symbol.ai_decision.candidate_weight ?? symbol.candidate_selection.candidate_weight ?? "-"
          } / 여유 사유 ${
            symbol.ai_decision.capacity_reason ?? symbol.candidate_selection.capacity_reason ?? "-"
          }`,
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.15fr,0.85fr]">
        <div className="grid gap-4 lg:grid-cols-3 xl:col-span-2">
          {valueCard("AI 의견", recommendation.label, recommendation.detail)}
          {valueCard("신규 진입 판단", riskOutcome.label, riskOutcome.detail)}
          {valueCard("주문 상태", executionOutcome.label, executionOutcome.detail)}
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass("neutral")}`}>
              AI 의견
            </span>
            <span className="text-xs text-slate-500">{formatDateTime(symbol.ai_decision.created_at)}</span>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <p className="text-2xl font-semibold text-slate-950">{translateDecision(symbol.ai_decision.decision)}</p>
            <p className="text-sm text-slate-600">
              신뢰도{" "}
              {symbol.ai_decision.confidence !== null ? formatRatio(symbol.ai_decision.confidence) : "-"}
            </p>
          </div>
          <p className="mt-3 text-sm leading-6 text-slate-700">
            {symbol.ai_decision.explanation_short ?? "최신 AI 의견 설명이 없습니다."}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {symbol.ai_decision.rationale_codes.length === 0 ? (
                <span className="text-sm text-slate-500">근거 없음</span>
              ) : (
              symbol.ai_decision.rationale_codes.map((code) => (
                <span key={code} className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700">
                  {translateReasonCode(code)}
                </span>
              ))
            )}
          </div>
        </div>
        {detailList([
          ["AI 제공자", symbol.ai_decision.provider_name ?? "-"],
          ["호출 이벤트", symbol.ai_decision.trigger_event ?? "-"],
          ["마지막 AI 호출 사유", translateAiTriggerReason(symbol.ai_decision.last_ai_trigger_reason)],
          ["마지막 AI 미호출 사유", translateAiSkipReason(symbol.ai_decision.last_ai_skip_reason)],
          ["다음 AI 검토 예정", formatDateTime(symbol.ai_decision.next_ai_review_due_at)],
          ["배정 슬롯", symbol.ai_decision.assigned_slot ?? symbol.candidate_selection.assigned_slot ?? "-"],
          [
            "우선순위 가중치",
            String(symbol.ai_decision.candidate_weight ?? symbol.candidate_selection.candidate_weight ?? "-"),
          ],
          ["슬롯 배정 이유", symbol.ai_decision.capacity_reason ?? symbol.candidate_selection.capacity_reason ?? "-"],
          ["기준 타임프레임", symbol.ai_decision.timeframe ?? symbol.timeframe ?? "-"],
          ["판단 기록 ID", String(symbol.ai_decision.decision_run_id ?? "-")],
        ])}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr,0.9fr]">
        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(riskOutcome.kind)}`}>
              {riskOutcome.label}
            </span>
            <span className="text-xs text-slate-500">{formatDateTime(symbol.risk_guard.created_at)}</span>
          </div>
          <p className="mt-4 text-2xl font-semibold text-slate-950">{translateDecision(symbol.risk_guard.decision)}</p>
          <p className="mt-2 text-sm text-slate-600">
            운영 상태 {translateOperatingState(symbol.risk_guard.operating_state)}
          </p>
          <div className="mt-4 space-y-2">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">막힌 이유</p>
            {blockedReasons.length === 0 ? (
              <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
                최근 신규 진입 차단 사유는 없습니다.
              </div>
            ) : (
              blockedReasons.map((code) => (
                <div key={code} className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-800">
                  {translateReasonCode(code)}
                </div>
              ))
            )}
          </div>
          {adjustmentReasons.length > 0 ? (
            <div className="mt-4 space-y-2">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">크기 조정 이유</p>
              {adjustmentReasons.map((code) => (
                <div key={code} className="rounded-2xl bg-amber-50 px-4 py-3 text-sm text-amber-900">
                  {translateReasonCode(code)}
                </div>
              ))}
            </div>
          ) : null}
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {valueCard(
            "허용된 거래 리스크",
            symbol.risk_guard.approved_risk_pct !== null ? formatRatio(symbol.risk_guard.approved_risk_pct) : "-",
            "한 번의 거래에서 허용된 최대 리스크입니다.",
          )}
          {valueCard(
            "허용된 최대 레버리지",
            symbol.risk_guard.approved_leverage !== null
              ? `${formatNumber(symbol.risk_guard.approved_leverage, 2)}x`
              : "-",
            "안전 규칙 기준으로 허용된 최대 레버리지입니다.",
          )}
          {valueCard(
            "허용된 진입 금액",
            symbol.risk_guard.approved_projected_notional !== null
              ? formatNumber(symbol.risk_guard.approved_projected_notional, 2)
              : "-",
            autoResized
              ? `축소 비율 ${
                  symbol.risk_guard.size_adjustment_ratio !== null
                    ? formatRatio(symbol.risk_guard.size_adjustment_ratio)
                    : "-"
                }`
              : "안전 점검 기준 최종 진입 금액입니다.",
          )}
          {valueCard(
            "허용된 최종 수량",
            symbol.risk_guard.approved_quantity !== null
              ? formatNumber(symbol.risk_guard.approved_quantity, 6)
              : "-",
            "주문이 그대로 따라야 하는 최종 수량입니다.",
          )}
          {valueCard(
            "자동 축소",
            autoResized ? "적용" : "없음",
            autoResized ? "리스크 여유 한도에 맞춰 신규 진입 크기를 자동 축소했습니다." : "원래 요청 크기가 그대로 승인되었습니다.",
          )}
          {valueCard(
            "판단 기준 스냅샷",
            symbol.risk_guard.snapshot_id !== null ? String(symbol.risk_guard.snapshot_id) : "-",
            symbol.risk_guard.as_of ? `기준 시각 ${formatDateTime(symbol.risk_guard.as_of)}` : "이번 판단 주기 기준 시각입니다.",
          )}
          {valueCard(
            "추가 진입 가능 여유",
            symbol.risk_guard.exposure_headroom_snapshot.limiting_headroom_notional !== undefined
              ? formatNumber(symbol.risk_guard.exposure_headroom_snapshot.limiting_headroom_notional, 2)
              : "-",
            symbol.risk_guard.auto_resize_reason
              ? translateAutoResizeReason(symbol.risk_guard.auto_resize_reason)
              : "가장 먼저 닿는 노출 한도 기준입니다.",
          )}
          {valueCard(
            "배정 슬롯",
            symbol.risk_guard.assigned_slot ?? symbol.candidate_selection.assigned_slot ?? "-",
            `가중치 ${
              symbol.risk_guard.candidate_weight ?? symbol.candidate_selection.candidate_weight ?? "-"
            }`,
          )}
          {valueCard(
            "슬롯 완충 한도",
            symbol.risk_guard.portfolio_slot_soft_cap_applied ? "적용" : "미적용",
            symbol.risk_guard.capacity_reason ?? symbol.candidate_selection.capacity_reason ?? "-",
          )}
        </div>
        <details className="rounded-2xl border border-slate-200 bg-slate-50 p-4 sm:col-span-2">
          <summary className="cursor-pointer list-none text-sm font-semibold text-slate-950">
            신규 진입 판단 수치
          </summary>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {valueCard(
              "원래 계산된 진입 금액",
              symbol.risk_guard.raw_projected_notional !== null
                ? formatNumber(symbol.risk_guard.raw_projected_notional, 2)
                : "-",
              "안전 점검 전 기준으로 계산된 진입 금액입니다.",
            )}
            {valueCard(
              "축소 비율",
              symbol.risk_guard.size_adjustment_ratio !== null
                ? formatRatio(symbol.risk_guard.size_adjustment_ratio)
                : "-",
              autoResized ? "원래 요청 크기 대비 최종 허용 크기 비율입니다." : "축소가 적용되지 않았습니다.",
            )}
          </div>
        </details>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr,0.8fr]">
        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                symbol.execution.order_status === "filled"
                  ? "good"
                  : symbol.execution.order_id
                    ? "warn"
                    : "neutral",
              )}`}
            >
              {symbol.execution.order_id ? "실제 실행 상태" : "실행 기록 없음"}
            </span>
            <span className="text-xs text-slate-500">{formatDateTime(symbol.execution.created_at)}</span>
          </div>
          {symbol.execution.order_id ? (
            <div className="mt-4 space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                {valueCard(
                  "주문 상태",
                  `${symbol.execution.order_type ?? "-"} / ${symbol.execution.order_status ?? "-"}`,
                  `${symbol.execution.symbol ?? "-"} / ${translateDecision(symbol.execution.side ?? "-")}`,
                )}
                {valueCard(
                  "체결 상태",
                  symbol.execution.execution_status ?? "체결 없음",
                  `평균 체결가 ${
                    symbol.execution.average_fill_price !== null
                      ? formatNumber(symbol.execution.average_fill_price, 2)
                      : "-"
                  }`,
                )}
                {valueCard(
                  "요청 / 체결 수량",
                  `${symbol.execution.requested_quantity ?? 0} / ${symbol.execution.filled_quantity ?? 0}`,
                  "부분 체결 여부 확인",
                )}
                {valueCard(
                  "주문 처리 품질",
                  String(symbol.execution.execution_quality.execution_quality_status ?? "-"),
                  String(symbol.execution.execution_quality.decision_quality_status ?? "판단 품질 정보 없음"),
                )}
              </div>
              <div className="rounded-2xl bg-slate-50 p-4">
                <div className="flex items-center justify-between gap-3">
                  <h4 className="text-sm font-semibold text-slate-950">최근 체결 요약</h4>
                  <span className="text-xs text-slate-500">{symbol.execution.recent_fills.length}건</span>
                </div>
                {symbol.execution.recent_fills.length === 0 ? (
                  <p className="mt-3 text-sm text-slate-500">최근 체결 세부가 없습니다.</p>
                ) : (
                  <div className="mt-3 space-y-2">
                    {symbol.execution.recent_fills.map((fill) => (
                      <div key={fill.execution_id ?? fill.external_trade_id ?? fill.created_at ?? "fill"} className="rounded-xl bg-white px-3 py-3">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <p className="text-sm font-medium text-slate-900">
                            {fill.fill_quantity ?? 0} @ {fill.fill_price !== null ? formatNumber(fill.fill_price, 2) : "-"}
                          </p>
                          <span className="text-xs text-slate-500">{formatDateTime(fill.created_at)}</span>
                        </div>
                        <p className="mt-2 text-xs text-slate-600">
                          수수료 {fill.fee_paid !== null ? formatNumber(fill.fee_paid, 4) : "-"} {fill.commission_asset ?? ""}
                          {" / "}
                          실현 손익 {fill.realized_pnl !== null ? formatMoney(fill.realized_pnl) : "-"}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="mt-4 rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">
              {executionOutcome.detail}
            </div>
          )}
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-950">시장·보호 주문 요약</h3>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {valueCard(
              "시장 흐름",
              String(symbol.market_context_summary.primary_regime ?? "-"),
              `추세 정렬 ${String(symbol.market_context_summary.trend_alignment ?? "-")}`,
            )}
            {valueCard(
              "거래량 / 탄력",
              `${String(symbol.market_context_summary.volume_regime ?? "-")} / ${String(
                symbol.market_context_summary.momentum_state ?? "-",
              )}`,
              `거래량이 약한 상태: ${formatYesNo(Boolean(symbol.market_context_summary.weak_volume))}`,
            )}
            {valueCard(
              "이 심볼의 준비 상태",
              symbol.live_execution_ready ? "가능" : "주의",
              symbol.stale_flags.length === 0
                ? "전역 신규 진입 가능성과 동기화 상태 기준입니다."
                : symbol.stale_flags.map(translateSyncScope).join(", "),
            )}
            {valueCard(
              "보호 주문 개수",
              String(symbol.protection_status.protective_order_count),
              `손절 ${symbol.protection_status.has_stop_loss ? "있음" : "없음"} / 익절 ${
                symbol.protection_status.has_take_profit ? "있음" : "없음"
              }`,
            )}
            {valueCard(
              "보호 복구 상태",
              translateProtectionRecoveryStatus(symbol.protection_status.recovery_status),
              symbol.protection_status.auto_recovery_active
                ? `자동 복구 중 / 실패 ${symbol.protection_status.failure_count}회`
                : `실패 ${symbol.protection_status.failure_count}회`,
            )}
            {valueCard(
              "보호 주문 확인",
              translateProtectionVerificationStatus(
                symbol.protection_status.verification_status ?? (symbol.protection_status.protected ? "verified" : "-"),
              ),
              symbol.protection_status.last_event_type
                ? `${symbol.protection_status.last_event_type} / ${formatDateTime(symbol.protection_status.last_event_at)}`
                : "최근 보호 이벤트 없음",
            )}
            {valueCard(
              "보유 관점",
              symbol.open_position.holding_profile ?? symbol.ai_decision.holding_profile ?? "-",
              symbol.open_position.holding_profile_reason ?? symbol.ai_decision.holding_profile_reason ?? "-",
            )}
            {valueCard(
              "고정 손절",
              symbol.open_position.hard_stop_active ? "활성" : "비활성",
              symbol.open_position.stop_widening_allowed === false ? "손절 확대 금지" : "손절 확대 가능 여부는 아직 확인되지 않았습니다.",
            )}
          </div>
          {symbol.protection_status.last_error ? (
            <div className="mt-4 rounded-2xl bg-amber-50 px-4 py-3 text-sm text-amber-900">
              {symbol.protection_status.last_error}
            </div>
          ) : null}
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-semibold text-slate-950">최근 감사 기록</h3>
        <div className="mt-4 space-y-3">
          {symbol.audit_events.length === 0 ? (
            <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
              {symbol.symbol} 기준 최근 감사 기록이 없습니다.
            </div>
          ) : (
            symbol.audit_events.map((event) => (
              <div
                key={`${event.event_type}-${event.entity_id}-${event.created_at}`}
                className="rounded-2xl bg-slate-50 px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                      event.severity === "error"
                        ? "danger"
                        : event.severity === "warning"
                          ? "warn"
                          : "neutral",
                    )}`}
                  >
                    {translateSeverity(event.severity)}
                  </span>
                  <span className="text-xs text-slate-500">{formatDateTime(event.created_at)}</span>
                  <span className="text-xs text-slate-500">
                    {event.event_type} / {event.entity_type}:{event.entity_id}
                  </span>
                </div>
                <p className="mt-2 text-sm text-slate-800">{event.message}</p>
                {auditPayloadRows(event.payload).length > 0 ? (
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {auditPayloadRows(event.payload).map(([label, value]) => (
                      <div key={`${event.event_type}-${label}`} className="rounded-xl bg-white px-3 py-2">
                        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">{label}</p>
                        <p className="mt-1 text-sm text-slate-700">{value}</p>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

export function OverviewDashboard({ initial }: { initial: OperatorDashboardPayload }) {
  const [payload, setPayload] = useState(initial);
  const [lastUpdated, setLastUpdated] = useState(() => new Date());
  const [refreshError, setRefreshError] = useState("");
  const [isPending, startTransition] = useTransition();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const next = await fetchPayload();
        if (!active) {
          return;
        }
        setPayload(next);
        setLastUpdated(new Date());
        setRefreshError("");
      } catch (error) {
        if (!active) {
          return;
        }
        setRefreshError(error instanceof Error ? error.message : "대시보드 갱신에 실패했습니다.");
      }
    };
    const interval = window.setInterval(() => void refresh(), refreshIntervalMs);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const operator = payload;
  const selectedSymbol = useMemo(
    () =>
      resolveSelectedSymbol(
        searchParams.get("symbol"),
        operator.control.tracked_symbols,
        operator.control.default_symbol,
        { mode: "all" },
      ),
    [operator.control.default_symbol, operator.control.tracked_symbols, searchParams],
  );
  const selectedSymbolSummary =
    selectedSymbol === ALL_SYMBOLS
      ? null
      : operator.symbols.find((item) => item.symbol === selectedSymbol) ?? null;
  const focusSymbolSummary = useMemo(
    () => resolveFocusSymbolSummary(operator.symbols, selectedSymbol, operator.control.default_symbol),
    [operator.control.default_symbol, operator.symbols, selectedSymbol],
  );
  const execution24h = operator.execution_windows.find((item) => item.window === "24h");

  const handleSymbolSelect = (value: string) => {
    startTransition(() => {
      const params = new URLSearchParams(searchParams.toString());
      if (value === ALL_SYMBOLS) {
        params.delete("symbol");
      } else {
        params.set("symbol", value);
      }
      const nextQuery = params.toString();
      router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
    });
  };

  return (
    <div className="space-y-6">
      <GlobalOperatorSummary
        control={operator.control}
        market={operator.market_signal}
        execution24h={execution24h}
        selectedSymbol={selectedSymbol}
        focusSymbol={focusSymbolSummary}
        globalAuditEvents={operator.audit_events}
      />

      <SymbolFilterBar
        symbols={operator.control.tracked_symbols}
        selectedSymbol={selectedSymbol}
        onSelect={handleSymbolSelect}
      />

      <SymbolStatusBoard
        symbols={operator.symbols}
        selectedSymbol={selectedSymbol}
        onSelect={handleSymbolSelect}
      />

      <SymbolDetailPanel selectedSymbol={selectedSymbol} symbol={selectedSymbolSummary} />

      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-500">
        <span>마지막 로컬 갱신 {lastUpdated.toLocaleTimeString("ko-KR", { hour12: false })}</span>
        {refreshError ? <span className="text-rose-700">{refreshError}</span> : null}
        {isPending ? <span>심볼 보기 전환 중</span> : null}
      </div>
    </div>
  );
}
