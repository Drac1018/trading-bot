"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ALL_SYMBOLS, filterSymbolsBySelection, resolveSelectedSymbol } from "../lib/selected-symbol";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const refreshIntervalMs = 15000;

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
};

type OperatorRiskSnapshot = {
  risk_check_id: number | null;
  decision_run_id: number | null;
  created_at: string | null;
  allowed: boolean | null;
  decision: string | null;
  operating_state: string | null;
  reason_codes: string[];
  approved_risk_pct: number | null;
  approved_leverage: number | null;
  raw_projected_notional: number | null;
  approved_projected_notional: number | null;
  approved_quantity: number | null;
  auto_resized_entry: boolean;
  size_adjustment_ratio: number | null;
  auto_resize_reason: string | null;
  exposure_headroom_snapshot: Record<string, number>;
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
  leverage: number | null;
};

type OperatorProtectionSummary = {
  status: string;
  protected: boolean;
  protective_order_count: number;
  has_stop_loss: boolean;
  has_take_profit: boolean;
  missing_components: string[];
};

type OperatorSymbolSummary = {
  symbol: string;
  timeframe: string | null;
  latest_price: number | null;
  market_snapshot_time: string | null;
  market_context_summary: Record<string, unknown>;
  ai_decision: OperatorDecisionSnapshot;
  risk_guard: OperatorRiskSnapshot;
  execution: OperatorExecutionSnapshot;
  open_position: OperatorPositionSummary;
  protection_status: OperatorProtectionSummary;
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
    sync_freshness_summary: Record<string, SyncScopeStatus>;
    protection_recovery_status: string;
    protected_positions: number;
    unprotected_positions: number;
    open_positions: number;
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

type Payload = { operator: OperatorDashboardPayload };

const decisionLabelMap: Record<string, string> = {
  hold: "보류",
  long: "롱",
  short: "숏",
  reduce: "축소",
  exit: "청산",
};

const operatingStateLabelMap: Record<string, string> = {
  TRADABLE: "신규 진입 가능",
  PROTECTION_REQUIRED: "보호 복구 우선",
  DEGRADED_MANAGE_ONLY: "관리 전용",
  EMERGENCY_EXIT: "비상 청산",
  PAUSED: "일시 중지",
};

const reasonCodeLabelMap: Record<string, string> = {
  TRADING_PAUSED: "운영 중지 상태",
  HOLD_DECISION: "보류 판단",
  LIVE_APPROVAL_REQUIRED: "실거래 승인 필요",
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
};

const syncScopeLabelMap: Record<string, string> = {
  account: "계좌",
  positions: "포지션",
  open_orders: "오더",
  protective_orders: "보호 주문",
  market_snapshot: "시장 스냅샷",
  market_snapshot_incomplete: "시장 스냅샷 불완전",
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
      label: "신규 진입 제안",
      detail: translateDecision(decision),
    };
  }
  if (isSurvivalDecision(decision)) {
    return {
      label: "생존 경로 제안",
      detail: translateDecision(decision),
    };
  }
  if (decision === "hold") {
    return {
      label: "보류 제안",
      detail: "신규 진입 없음",
    };
  }
  return {
    label: "추천 없음",
    detail: "-",
  };
}

function riskOutcomeSummary(symbol: OperatorSymbolSummary) {
  const decision = symbol.risk_guard.decision ?? symbol.ai_decision.decision;
  if (symbol.risk_guard.allowed === null) {
    return {
      label: "risk 평가 없음",
      detail: "-",
      kind: "neutral" as const,
    };
  }
  if (symbol.risk_guard.allowed) {
    if (isSurvivalDecision(decision)) {
      return {
        label: "생존 경로 허용",
        detail: translateDecision(decision),
        kind: "good" as const,
      };
    }
    if (isEntryDecision(decision)) {
      return {
        label: "신규 진입 승인",
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
      label: "생존 경로 차단",
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
    ? "생존 경로"
    : isEntryDecision(decision)
      ? "신규 진입"
      : "주문";

  if (!symbol.execution.order_id) {
    if (symbol.risk_guard.allowed === false) {
      return {
        label: "실행 없음",
        detail: "risk 차단으로 주문 없음",
        kind: "danger" as const,
      };
    }
    if (symbol.ai_decision.decision === "hold") {
      return {
        label: "실행 없음",
        detail: "AI 보류 제안",
        kind: "neutral" as const,
      };
    }
    return {
      label: "주문 없음",
      detail: "아직 실행 기록 없음",
      kind: "neutral" as const,
    };
  }

  if (symbol.execution.order_status === "filled" || symbol.execution.execution_status === "filled") {
    return {
      label: `${flowLabel} 실행됨`,
      detail: executionStatus ?? "filled",
      kind: "good" as const,
    };
  }

  return {
    label: `${flowLabel} 주문 제출`,
    detail: executionStatus ?? "pending",
    kind: "warn" as const,
  };
}

function translateOperatingState(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return operatingStateLabelMap[value] ?? value;
}

function translateReasonCode(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const extraReasonCodeLabelMap: Record<string, string> = {
    ENTRY_AUTO_RESIZED: "리스크 한도 내 자동 축소 진입",
    ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT: "총 노출도 한도에 맞춘 자동 축소",
    ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT: "방향 편중 한도에 맞춘 자동 축소",
    ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT: "단일 포지션 한도에 맞춘 자동 축소",
    ENTRY_CLAMPED_TO_SAME_TIER_LIMIT: "동일 티어 집중도 한도에 맞춘 자동 축소",
    ENTRY_SIZE_BELOW_MIN_NOTIONAL: "최소 실행 가능 주문 미만",
    ENTRY_TRIGGER_NOT_MET: "진입 트리거 미충족",
    CHASE_LIMIT_EXCEEDED: "추격 진입 한도 초과",
    INVALID_INVALIDATION_PRICE: "무효화 가격 기준 이상",
  };
  return extraReasonCodeLabelMap[value] ?? reasonCodeLabelMap[value] ?? value;
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

function translateSyncScope(value: string) {
  return syncScopeLabelMap[value] ?? value;
}

function syncBadge(scope: SyncScopeStatus | undefined) {
  if (!scope) {
    return { label: "미확인", kind: "warn" as const };
  }
  if (scope.incomplete) {
    return { label: "불완전", kind: "danger" as const };
  }
  if (scope.stale) {
    return { label: "Stale", kind: "warn" as const };
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

async function fetchPayload(): Promise<Payload> {
  const response = await fetch(`${apiBaseUrl}/api/dashboard/operator`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || response.statusText);
  }
  return { operator: (await response.json()) as OperatorDashboardPayload };
}

function filteredBlockedReasons(symbol: OperatorSymbolSummary) {
  return symbol.blocked_reasons.filter((item, index, array) => array.indexOf(item) === index);
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
}: {
  control: OperatorDashboardPayload["control"];
  market: OperatorDashboardPayload["market_signal"];
  execution24h: ExecutionWindow | undefined;
  selectedSymbol: string;
}) {
  const status = control.trading_paused
    ? {
        kind: "danger" as const,
        label: "신규 진입 차단",
        detail: translateReasonCode(control.pause_reason_code),
      }
    : !control.live_execution_ready
      ? {
          kind: "warn" as const,
          label: "가드 모드",
          detail: control.guard_mode_reason_message ?? "진입 조건을 아직 충족하지 못했습니다.",
        }
      : {
          kind: "good" as const,
          label: "신규 진입 가능",
          detail: "전역 운영 상태 기준으로 신규 진입과 기존 포지션 관리가 가능합니다.",
        };

  const primaryWindow = market.performance_windows[0];
  const syncScopes = [
    ["account", "계좌"],
    ["positions", "포지션"],
    ["open_orders", "오더"],
    ["protective_orders", "보호 주문"],
  ] as const;

  return (
    <section className="space-y-6 rounded-[2rem] border border-amber-200/70 bg-white/90 p-6 shadow-frame sm:p-7">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.34em] text-slate-500">
            Global Operator Summary
          </p>
          <h1 className="mt-3 font-display text-3xl leading-tight text-slate-950 sm:text-4xl">
            전역 운영 상태와 심볼별 현황
          </h1>
          <p className="mt-4 max-w-3xl text-sm leading-7 text-slate-600 sm:text-base">
            이 화면이 운영 상태의 단일 기준 화면입니다. 상단은 계좌와 시스템 전역 상태만 보여주고,
            그 아래에서 심볼별 AI 판단, risk 결과, 실행 상태를 분리해서 확인합니다.
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
          <span>자동 복구 {control.auto_resume_status}</span>
          <span>보호 복구 {control.protection_recovery_status}</span>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-4">
        {valueCard(
          "신규 진입",
          control.can_enter_new_position ? "가능" : "차단",
          control.guard_mode_reason_message ?? "전역 운영 상태 기준",
        )}
        {valueCard(
          "실거래 승인",
          control.approval_armed ? (control.approval_expires_at ? "유효" : "무기한 승인") : "승인 필요",
          control.approval_armed
            ? control.approval_expires_at
              ? `만료 ${formatDateTime(control.approval_expires_at)}`
              : "승인 유지 시간 제한 없음"
            : "승인 창을 다시 열어야 합니다.",
        )}
        {valueCard(
          "열린 포지션",
          `${control.open_positions}`,
          `보호됨 ${control.protected_positions} / 미보호 ${control.unprotected_positions}`,
        )}
        {valueCard(
          "손익",
          formatMoney(control.daily_pnl),
          `누적 ${formatMoney(control.cumulative_pnl)}`,
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-2xl border border-slate-200 bg-white">
          {[
            ["기본 심볼 / 타임프레임", `${control.default_symbol} / ${control.default_timeframe}`],
            ["추적 심볼", control.tracked_symbols.join(", ")],
            ["pause 사유", translateReasonCode(control.pause_reason_code)],
            ["pause origin", control.pause_origin ?? "-"],
            ["자동 복구 가능", control.auto_resume_eligible ? "가능" : "불가"],
            ["자동 복구 예정", formatDateTime(control.auto_resume_after)],
            [
              "스케줄러",
              control.scheduler_window || control.scheduler_status
                ? `${control.scheduler_window ?? "-"} / ${control.scheduler_status ?? "-"}`
                : "-",
            ],
            ["다음 실행 예정", formatDateTime(control.scheduler_next_run_at)],
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
          <h3 className="text-sm font-semibold text-slate-950">전역 차단 사유</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            hold 증가 조건과 별도로, 실제로 신규 진입을 막는 운영/리스크 사유만 보여줍니다.
          </p>
          <div className="mt-4 space-y-2">
            {[...control.latest_blocked_reasons, ...control.auto_resume_last_blockers]
              .filter((item, index, array) => array.indexOf(item) === index)
              .map((reason) => (
                <div key={reason} className="rounded-2xl bg-amber-50 px-4 py-3 text-sm text-slate-800">
                  {translateReasonCode(reason)}
                </div>
              ))}
            {control.latest_blocked_reasons.length === 0 && control.auto_resume_last_blockers.length === 0 ? (
              <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
                현재 전역 차단 사유는 없습니다.
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-semibold text-slate-950">거래소 상태 동기화 freshness</h3>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          계좌, 포지션, 오더, 보호 주문 중 하나라도 stale 또는 불완전이면 신규 진입은 차단될 수
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
                  <p>마지막 성공 {formatDateTime(scope?.last_sync_at)}</p>
                  <p>경과 {formatFreshnessSeconds(scope?.freshness_seconds ?? null)}</p>
                  <p>임계 {formatFreshnessSeconds(scope?.stale_after_seconds ?? null)}</p>
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
            시장 / 신호 요약
          </p>
          <h2 className="mt-2 text-xl font-semibold text-slate-950">최근 성과와 실행 품질</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            상위 성과 신호, 레짐별 성과, 슬리피지와 부분 체결 품질을 최근 윈도우 기준으로 확인합니다.
          </p>
        </div>

        <div className="grid gap-4 xl:grid-cols-3">
          {market.performance_windows.map((window) => {
            const holdRatio =
              window.summary.decisions > 0 ? window.summary.holds / window.summary.decisions : 0;
            const winRate =
              window.summary.wins + window.summary.losses > 0
                ? window.summary.wins / (window.summary.wins + window.summary.losses)
                : 0;
            return (
              <div key={window.window_label} className="rounded-2xl border border-slate-200 bg-white p-4">
                <h3 className="text-sm font-semibold text-slate-950">{window.window_label} 성과 요약</h3>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  {valueCard("순실현 손익", formatMoney(window.summary.net_realized_pnl_total), `수수료 ${formatMoney(window.summary.fee_total)}`)}
                  {valueCard("승률", formatRatio(winRate), `승 ${window.summary.wins} / 패 ${window.summary.losses}`)}
                  {valueCard("보류 비중", formatRatio(holdRatio), `보류 ${window.summary.holds} / 판단 ${window.summary.decisions}`)}
                  {valueCard(
                    "실행 품질",
                    `${formatNumber(Number(execution24h?.execution_quality_summary.average_realized_slippage_pct ?? 0), 2)}%`,
                    window.window_label === "24h" ? "평균 실슬리피지" : "전역 실행 품질은 24h 기준으로 집계",
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
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

        <div className="grid gap-4 xl:grid-cols-2">
          {performanceRows("레짐별 성과", primaryWindow?.top_regimes ?? [], "레짐별 집계가 없습니다.")}
          {performanceRows("심볼별 성과", primaryWindow?.top_symbols ?? [], "심볼별 집계가 없습니다.")}
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <h3 className="text-sm font-semibold text-slate-950">hold 증가 원인</h3>
            <div className="mt-4 space-y-2">
              {market.hold_blocked_summary.hold_top_conditions.length === 0 ? (
                <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
                  최근 hold 집중 조건은 없습니다.
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
            <h3 className="text-sm font-semibold text-slate-950">Adaptive 개입 상태</h3>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {valueCard(
                "상태",
                String(market.adaptive_signal_summary.status ?? "disabled"),
                `입력 ${Array.isArray(market.adaptive_signal_summary.active_inputs) ? market.adaptive_signal_summary.active_inputs.join(", ") || "없음" : "없음"}`,
              )}
              {valueCard(
                "signal weight",
                formatNumber(Number(market.adaptive_signal_summary.signal_weight ?? 1), 2),
                "최근 성과 기반 보수 조정치",
              )}
              {valueCard(
                "confidence 배수",
                `${formatNumber(Number(market.adaptive_signal_summary.confidence_multiplier ?? 1), 2)}x`,
                "손실 구간에서는 1보다 작아집니다.",
              )}
              {valueCard(
                "hold bias",
                formatNumber(Number(market.adaptive_signal_summary.hold_bias ?? 0), 2),
                `risk 배수 ${formatNumber(Number(market.adaptive_signal_summary.risk_pct_multiplier ?? 1), 2)}x`,
              )}
            </div>
          </div>
        </div>
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
      <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">Symbol Filter</p>
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
        전체 모드에서는 심볼별 핵심 상태를 비교하고, 개별 심볼을 선택하면 AI 판단, risk 결과,
        실행 결과, 감사 이벤트를 해당 심볼 기준으로만 상세하게 보여줍니다.
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
      <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">Symbol Status Board</p>
      <h2 className="mt-2 text-xl font-semibold text-slate-950">심볼별 운영 상태 비교</h2>
      <p className="mt-2 text-sm leading-6 text-slate-600">
        각 행은 하나의 심볼만 나타냅니다. AI 제안, risk 차단, 실행 상태가 서로 다른 심볼과 섞이지
        않도록 최신 스냅샷을 분리해서 보여줍니다.
      </p>
      <div className="mt-5 overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-y-2">
          <thead>
            <tr className="text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
              <th className="px-3 py-2">심볼</th>
              <th className="px-3 py-2">현재가</th>
              <th className="px-3 py-2">AI 추천</th>
              <th className="px-3 py-2">신뢰도</th>
              <th className="px-3 py-2">risk 결과</th>
              <th className="px-3 py-2">risk 차단 사유</th>
              <th className="px-3 py-2">포지션</th>
              <th className="px-3 py-2">보호 상태</th>
              <th className="px-3 py-2">실제 실행</th>
              <th className="px-3 py-2">마지막 갱신</th>
            </tr>
          </thead>
          <tbody>
            {visibleSymbols.map((item) => {
              const blockedReasons = filteredBlockedReasons(item);
              const recommendation = recommendationSummary(item.ai_decision.decision);
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
                      ? `${item.open_position.side ?? "-"} / ${item.open_position.quantity ?? 0}`
                      : "없음"}
                  </td>
                  <td className="px-3 py-3 text-sm text-slate-700">
                    {item.protection_status.status}
                    {item.protection_status.missing_components.length > 0
                      ? ` (${item.protection_status.missing_components.join(", ")})`
                      : ""}
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
  globalAuditEvents,
}: {
  selectedSymbol: string;
  symbol: OperatorSymbolSummary | null;
  globalAuditEvents: AuditEvent[];
}) {
  if (selectedSymbol === ALL_SYMBOLS || symbol === null) {
    return (
      <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">Symbol Detail Panel</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">심볼 상세</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          전체 모드에서는 심볼 비교만 보여줍니다. 아래 상세 흐름은 특정 심볼을 선택했을 때만
          표시됩니다.
        </p>
        <div className="mt-5 rounded-2xl border border-dashed border-slate-300 px-4 py-5 text-sm text-slate-600">
          상세 AI 판단, risk_guard 결과, 실행 결과, 감사 이벤트를 보려면 심볼을 선택하세요.
        </div>
        <div className="mt-5 rounded-2xl border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-950">최근 전역 감사 이벤트</h3>
          <div className="mt-4 space-y-3">
            {globalAuditEvents.length === 0 ? (
              <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
                최근 전역 감사 이벤트가 없습니다.
              </div>
            ) : (
              globalAuditEvents.map((event) => (
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
                  </div>
                  <p className="mt-2 text-sm text-slate-800">{event.message}</p>
                </div>
              ))
            )}
          </div>
        </div>
      </section>
    );
  }

  const blockedReasons = filteredBlockedReasons(symbol);
  const autoResized = symbol.risk_guard.auto_resized_entry;
  const recommendation = recommendationSummary(symbol.ai_decision.decision);
  const riskOutcome = riskOutcomeSummary(symbol);
  const executionOutcome = executionOutcomeSummary(symbol);

  return (
    <section className="space-y-6 rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">
          Symbol Detail Panel
        </p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">{symbol.symbol} 상세 운영 흐름</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          아래 정보는 선택한 심볼의 최신 AI 추천, risk_guard 결과, 실제 실행 상태, 감사 이벤트만
          보여줍니다. 차단 사유는 AI 설명이 아니라 risk 결과 블록에서만 해석합니다.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-4">
        {valueCard(
          "현재가",
          symbol.latest_price !== null ? formatNumber(symbol.latest_price, 2) : "-",
          `마켓 스냅샷 ${formatDateTime(symbol.market_snapshot_time)}`,
        )}
        {valueCard(
          "데이터 상태",
          symbol.stale_flags.length === 0 ? "정상" : "주의",
          symbol.stale_flags.length === 0
            ? "계좌/포지션/오더/시장 스냅샷이 모두 정상 범위입니다."
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
          symbol.protection_status.status,
          symbol.protection_status.missing_components.length > 0
            ? `누락 ${symbol.protection_status.missing_components.join(", ")}`
            : "보호 주문 구성 정상",
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.15fr,0.85fr]">
        <div className="grid gap-4 lg:grid-cols-3 xl:col-span-2">
          {valueCard("AI 추천", recommendation.label, recommendation.detail)}
          {valueCard("risk 결과", riskOutcome.label, riskOutcome.detail)}
          {valueCard("실제 실행", executionOutcome.label, executionOutcome.detail)}
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass("neutral")}`}>
              AI 제안
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
            {symbol.ai_decision.explanation_short ?? "최신 AI 제안 설명이 없습니다."}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {symbol.ai_decision.rationale_codes.length === 0 ? (
              <span className="text-sm text-slate-500">rationale code 없음</span>
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
          ["provider", symbol.ai_decision.provider_name ?? "-"],
          ["trigger event", symbol.ai_decision.trigger_event ?? "-"],
          ["timeframe", symbol.ai_decision.timeframe ?? symbol.timeframe ?? "-"],
          ["decision run id", String(symbol.ai_decision.decision_run_id ?? "-")],
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
            {blockedReasons.length === 0 ? (
              <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
                최신 risk 차단 사유는 없습니다. 허용 또는 보류 상태입니다.
              </div>
            ) : (
              blockedReasons.map((code) => (
                <div key={code} className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-800">
                  {translateReasonCode(code)}
                </div>
              ))
            )}
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {valueCard(
            "승인 risk_pct",
            symbol.risk_guard.approved_risk_pct !== null ? formatRatio(symbol.risk_guard.approved_risk_pct) : "-",
            "최종 허용된 거래당 리스크",
          )}
          {valueCard(
            "승인 leverage",
            symbol.risk_guard.approved_leverage !== null
              ? `${formatNumber(symbol.risk_guard.approved_leverage, 2)}x`
              : "-",
            "결정론적 리스크 엔진이 허용한 최대 레버리지",
          )}
          {valueCard(
            "raw projected notional",
            symbol.risk_guard.raw_projected_notional !== null
              ? formatNumber(symbol.risk_guard.raw_projected_notional, 2)
              : "-",
            "원래 계산된 신규 진입 예상 notional",
          )}
          {valueCard(
            "approved projected notional",
            symbol.risk_guard.approved_projected_notional !== null
              ? formatNumber(symbol.risk_guard.approved_projected_notional, 2)
              : "-",
            autoResized
              ? `축소 비율 ${
                  symbol.risk_guard.size_adjustment_ratio !== null
                    ? formatRatio(symbol.risk_guard.size_adjustment_ratio)
                    : "-"
                }`
              : "리스크 승인 기준 최종 notional",
          )}
          {valueCard(
            "approved quantity",
            symbol.risk_guard.approved_quantity !== null
              ? formatNumber(symbol.risk_guard.approved_quantity, 6)
              : "-",
            "execution이 그대로 따라야 하는 최종 수량",
          )}
          {valueCard(
            "자동 축소",
            autoResized ? "적용" : "없음",
            autoResized ? "리스크 여유 한도에 맞춰 신규 진입 크기를 자동 축소했습니다." : "원래 요청 크기가 그대로 승인되었습니다.",
          )}
          {valueCard(
            "축소 비율",
            symbol.risk_guard.size_adjustment_ratio !== null
              ? formatRatio(symbol.risk_guard.size_adjustment_ratio)
              : "-",
            autoResized ? "raw 요청 크기 대비 최종 승인 크기 비율입니다." : "축소가 적용되지 않았습니다.",
          )}
          {valueCard(
            "headroom",
            symbol.risk_guard.exposure_headroom_snapshot.limiting_headroom_notional !== undefined
              ? formatNumber(symbol.risk_guard.exposure_headroom_snapshot.limiting_headroom_notional, 2)
              : "-",
            symbol.risk_guard.auto_resize_reason
              ? translateAutoResizeReason(symbol.risk_guard.auto_resize_reason)
              : "가장 타이트한 노출도 여유 한도",
          )}
        </div>
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
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {valueCard(
                "주문 상태",
                `${symbol.execution.order_type ?? "-"} / ${symbol.execution.order_status ?? "-"}`,
                `${symbol.execution.symbol ?? "-"} / ${symbol.execution.side ?? "-"}`,
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
                "실행 품질",
                String(symbol.execution.execution_quality.execution_quality_status ?? "-"),
                String(symbol.execution.execution_quality.decision_quality_status ?? "판단 품질 정보 없음"),
              )}
            </div>
          ) : (
            <div className="mt-4 rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">
              {executionOutcome.detail}
            </div>
          )}
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-950">시장/보호 요약</h3>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {valueCard(
              "레짐",
              String(symbol.market_context_summary.primary_regime ?? "-"),
              `정렬 ${String(symbol.market_context_summary.trend_alignment ?? "-")}`,
            )}
            {valueCard(
              "볼륨 / 모멘텀",
              `${String(symbol.market_context_summary.volume_regime ?? "-")} / ${String(
                symbol.market_context_summary.momentum_state ?? "-",
              )}`,
              `weak_volume ${String(symbol.market_context_summary.weak_volume ?? false)}`,
            )}
            {valueCard(
              "심볼 readiness",
              symbol.live_execution_ready ? "가능" : "주의",
              symbol.stale_flags.length === 0 ? "전역 readiness와 동기화 상태 기준" : symbol.stale_flags.join(", "),
            )}
            {valueCard(
              "보호 주문 수",
              String(symbol.protection_status.protective_order_count),
              `stop ${symbol.protection_status.has_stop_loss ? "있음" : "없음"} / tp ${
                symbol.protection_status.has_take_profit ? "있음" : "없음"
              }`,
            )}
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-semibold text-slate-950">최근 감사 이벤트</h3>
        <div className="mt-4 space-y-3">
          {symbol.audit_events.length === 0 ? (
            <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
              {symbol.symbol} 기준 최근 감사 이벤트가 없습니다.
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
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

export function OverviewDashboard({ initial }: { initial: Payload }) {
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

  const operator = payload.operator;
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

      <SymbolDetailPanel
        selectedSymbol={selectedSymbol}
        symbol={selectedSymbolSummary}
        globalAuditEvents={operator.audit_events}
      />

      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-500">
        <span>마지막 로컬 갱신 {lastUpdated.toLocaleTimeString("ko-KR", { hour12: false })}</span>
        {refreshError ? <span className="text-rose-700">{refreshError}</span> : null}
        {isPending ? <span>심볼 보기 전환 중</span> : null}
      </div>
    </div>
  );
}
