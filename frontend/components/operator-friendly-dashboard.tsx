"use client";

import { useEffect, useMemo, useState } from "react";

import type { OperatorDashboardPayload } from "./overview-dashboard";
import { lookupRiskReasonCode } from "../lib/risk-reason-copy.js";
import { normalizeSyncScopeStatus } from "../lib/sync-freshness";

type Tone = "safe" | "warn" | "danger" | "neutral" | "info";
type TabId = "today" | "positions" | "audit";
type SymbolSummary = OperatorDashboardPayload["symbols"][number];
type AuditEvent = OperatorDashboardPayload["audit_events"][number];

type ActionItem = {
  id: string;
  title: string;
  detail: string;
  symbol: string;
  priority: "높음" | "보통";
  tone: Tone;
};

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const refreshIntervalMs = 15000;
const syncCatchUpIntervalMs = 2500;
const syncCatchUpMaxAttempts = 8;

const tradingSyncBlockers = new Set([
  "ACCOUNT_STATE_STALE",
  "POSITION_STATE_STALE",
  "OPEN_ORDERS_STATE_STALE",
  "PROTECTION_STATE_UNVERIFIED",
]);

const recoverableSyncStatuses = new Set(["stale", "skipped", "unknown"]);

const tabs: { id: TabId; label: string }[] = [
  { id: "today", label: "오늘 할 일" },
  { id: "positions", label: "포지션" },
  { id: "audit", label: "감사 로그" },
];

const passiveBlockers = new Set([
  "HOLD_DECISION",
  "ENTRY_TRIGGER_NOT_MET",
  "NO_EDGE",
  "RANGE_CHOP",
  "WEAK_VOLUME",
  "MOMENTUM_WEAKENING",
]);

const reasonFallbackMap: Record<string, string> = {
  MANUAL_USER_REQUEST: "운영자가 수동으로 거래를 일시정지했습니다.",
  TRADING_PAUSED: "거래가 일시정지되어 신규 진입을 차단했습니다.",
  PROTECTIVE_ORDER_FAILURE: "보호주문 이상이 있어 신규 진입을 막고 있습니다.",
  MISSING_PROTECTIVE_ORDERS: "미보호 포지션이 있어 보호주문 확인이 필요합니다.",
  PROTECTION_REQUIRED: "보호주문 복구가 끝나기 전까지 신규 진입을 막습니다.",
  DEGRADED_MANAGE_ONLY: "관리 전용 상태라 신규 진입보다 보호와 정리를 우선합니다.",
  EMERGENCY_EXIT: "비상 청산 상태라 신규 진입을 막습니다.",
  HOLD_DECISION: "현재 AI 판단은 새 포지션을 열지 않는 쪽입니다.",
  ENTRY_TRIGGER_NOT_MET: "현재 진입 조건이 아직 충족되지 않았습니다.",
};

const auditTitleMap: Record<string, string> = {
  exchange_sync_cycle_completed: "거래소 상태 동기화 완료",
  background_exchange_polling_sync_completed: "거래소 상태 자동 확인 완료",
  trading_paused: "거래 일시정지",
  trading_resumed: "거래 일시정지 해제",
  live_sync: "거래소 상태 동기화 완료",
  trading_auto_resume_skipped: "자동 재개 보류",
  live_approval_armed: "실거래 승인 열림",
  live_approval_disarmed: "실거래 승인 닫힘",
  protection_recreate_attempted: "보호주문 재생성 시도",
  protection_recreate_failed: "보호주문 재생성 실패",
  protected_recreated: "보호주문 재생성 완료",
  unprotected_position_detected: "미보호 포지션 감지",
};

const auditMessageTitleMap: Record<string, string> = {
  "Exchange sync cycle completed.": "거래소 상태 동기화 완료",
  "Background exchange polling sync completed.": "거래소 상태 자동 확인 완료",
  "Live exchange state synchronized.": "거래소 상태 동기화 완료",
  "Trading auto resume was skipped.": "자동 재개 보류",
  "Interval decision cycle skipped because no trigger was detected.": "검토할 변화 없음",
  "No deterministic entry or review trigger was detected for this interval cycle.": "검토할 진입 신호 없음",
};

function unique(values: string[]) {
  return [...new Set(values.filter((value) => value.trim().length > 0))];
}

function translateReasonCode(value: string | null | undefined) {
  if (!value) {
    return "추가 사유 없음";
  }
  return lookupRiskReasonCode(value) ?? reasonFallbackMap[value] ?? value;
}

function formatDateTime(value: string | null | undefined, options?: { seconds?: boolean }) {
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
    second: options?.seconds ? "2-digit" : undefined,
    hour12: false,
  }).format(parsed);
}

function formatTime(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value.endsWith("Z") ? value : `${value}Z`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function formatNumber(value: number | null | undefined, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatMoney(value: number | null | undefined, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${formatNumber(value, digits)} USDT`;
}

function formatPct(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${formatNumber(value * 100, 1)}%`;
}

function nestedNumber(source: Record<string, unknown>, path: string[]) {
  let current: unknown = source;
  for (const key of path) {
    if (!current || typeof current !== "object" || Array.isArray(current)) {
      return null;
    }
    current = (current as Record<string, unknown>)[key];
  }
  return typeof current === "number" ? current : null;
}

function controlBlockers(control: OperatorDashboardPayload["control"]) {
  const currentCycle = control.control_status_summary?.blocked_reasons_current_cycle ?? [];
  return unique([...currentCycle, ...control.latest_blocked_reasons, ...control.auto_resume_last_blockers]);
}

function importantBlockers(control: OperatorDashboardPayload["control"]) {
  return controlBlockers(control).filter((code) => !passiveBlockers.has(code));
}

function hasTradingSyncBlocker(control: OperatorDashboardPayload["control"]) {
  return importantBlockers(control).some((code) => tradingSyncBlockers.has(code));
}

function hasRecoverableSyncDelay(control: OperatorDashboardPayload["control"]) {
  return Object.values(control.sync_freshness_summary).some((status) =>
    recoverableSyncStatuses.has(normalizeSyncScopeStatus(status)),
  );
}

function needsSyncCatchUp(control: OperatorDashboardPayload["control"]) {
  return hasTradingSyncBlocker(control) || hasRecoverableSyncDelay(control);
}

function syncBlockerDetail(code: string) {
  const base = translateReasonCode(code);
  if (tradingSyncBlockers.has(code)) {
    return `${base} 잔고 표시와 별도로 포지션/주문 기준을 자동으로 다시 맞추는 중입니다.`;
  }
  return base;
}

function protectionLabel(control: OperatorDashboardPayload["control"]) {
  if (control.unprotected_positions > 0) {
    return { label: "확인 필요", tone: "warn" as const };
  }
  if (control.protected_positions > 0) {
    return { label: "정상", tone: "safe" as const };
  }
  if (control.open_positions === 0) {
    return { label: "대상 없음", tone: "neutral" as const };
  }
  return { label: "확인 중", tone: "warn" as const };
}

function mainState(operator: OperatorDashboardPayload) {
  const control = operator.control;
  const blockers = importantBlockers(control);
  const passiveOnly = !control.can_enter_new_position && blockers.length === 0;

  if (control.trading_paused) {
    return {
      title: "거래 일시정지",
      detail: translateReasonCode(control.pause_reason_code) || "운영자가 자동 거래를 일시정지했습니다.",
      tone: "danger" as const,
    };
  }

  if (control.can_enter_new_position) {
    return {
      title: "신규 진입 가능",
      detail: "계좌, 동기화, 보호주문 기준이 새 주문을 허용하는 상태입니다.",
      tone: "safe" as const,
    };
  }

  if (passiveOnly) {
    return {
      title: "신규 진입 없음",
      detail: "현재 조건에서는 새 포지션을 열지 않습니다.",
      tone: "neutral" as const,
    };
  }

  if (needsSyncCatchUp(control)) {
    return {
      title: "진입 안전 확인 중",
      detail: "잔고 화면은 최신이어도 신규 진입은 포지션, 미체결 주문, 보호주문 기준까지 다시 맞춘 뒤 허용됩니다.",
      tone: "warn" as const,
    };
  }

  return {
    title: "신규 진입 차단",
    detail: blockers.length > 0 ? translateReasonCode(blockers[0]) : control.guard_mode_reason_message ?? "안전 조건이 맞지 않아 새 주문을 막고 있습니다.",
    tone: "danger" as const,
  };
}

function syncSummary(control: OperatorDashboardPayload["control"]) {
  const scopes = control.sync_freshness_summary;
  const watched = ["account", "positions", "open_orders", "protective_orders"] as const;
  const statuses = watched.map((key) => normalizeSyncScopeStatus(scopes[key]));
  if (statuses.some((status) => status === "failed" || status === "incomplete")) {
    return { label: "확인 필요", tone: "danger" as const };
  }
  if (statuses.some((status) => recoverableSyncStatuses.has(status))) {
    return { label: "갱신 중", tone: "warn" as const };
  }
  return { label: "정상", tone: "safe" as const };
}

function syncActionTitle(status: string) {
  if (recoverableSyncStatuses.has(status)) {
    return "거래소 상태 다시 맞추는 중";
  }
  return "거래소 동기화 확인 필요";
}

function syncActionDetail(scope: string, status: string) {
  const label = translateSyncScope(scope);
  if (recoverableSyncStatuses.has(status)) {
    return `${label} 기준을 다시 확인 중입니다. 잔고 표시와 신규 진입 안전 기준은 갱신 시점이 다를 수 있습니다.`;
  }
  return `${label} 동기화 결과가 ${translateSyncStatus(status)} 상태입니다.`;
}

function buildActionItems(operator: OperatorDashboardPayload): ActionItem[] {
  const control = operator.control;
  const items: ActionItem[] = [];

  if (control.unprotected_positions > 0) {
    items.push({
      id: "unprotected-positions",
      title: "보호주문 확인 필요",
      detail: `${control.unprotected_positions}개 포지션의 보호주문이 확인되지 않았습니다.`,
      symbol: "전체",
      priority: "높음",
      tone: "warn",
    });
  }

  for (const code of importantBlockers(control).slice(0, 3)) {
    items.push({
      id: `blocker-${code}`,
      title: tradingSyncBlockers.has(code) ? "진입 안전 기준 갱신 중" : "신규 진입 차단 사유",
      detail: syncBlockerDetail(code),
      symbol: "전체",
      priority: ["PROTECTION_REQUIRED", ...tradingSyncBlockers].includes(code)
        ? "높음"
        : "보통",
      tone: "warn",
    });
  }

  for (const [scope, status] of Object.entries(control.sync_freshness_summary)) {
    const normalized = normalizeSyncScopeStatus(status);
    if (normalized === "synced") {
      continue;
    }
    items.push({
      id: `sync-${scope}`,
      title: syncActionTitle(normalized),
      detail: syncActionDetail(scope, normalized),
      symbol: "전체",
      priority: normalized === "failed" || normalized === "incomplete" ? "높음" : "보통",
      tone: normalized === "failed" || normalized === "incomplete" ? "danger" : "warn",
    });
  }

  for (const symbol of operator.symbols) {
    if (!symbol.open_position.is_open || symbol.protection_status.protected) {
      continue;
    }
    items.push({
      id: `protection-${symbol.symbol}`,
      title: `${symbol.symbol} 보호주문 미확정`,
      detail: "열린 포지션의 손절 주문이 거래소에 남아 있는지 확인이 필요합니다.",
      symbol: symbol.symbol,
      priority: "높음",
      tone: "warn",
    });
  }

  return uniqueById(items).slice(0, 5);
}

function uniqueById(items: ActionItem[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (seen.has(item.id)) {
      return false;
    }
    seen.add(item.id);
    return true;
  });
}

function translateSyncScope(scope: string) {
  const labels: Record<string, string> = {
    account: "계좌",
    positions: "포지션",
    open_orders: "미체결 주문",
    protective_orders: "보호주문",
    market_snapshot: "시장 데이터",
    market_snapshot_incomplete: "시장 데이터",
    feature_input_missing: "분석 입력",
  };
  return labels[scope] ?? scope;
}

function translateSyncStatus(status: string) {
  const labels: Record<string, string> = {
    stale: "지연",
    incomplete: "불완전",
    failed: "실패",
    skipped: "보류",
    unknown: "확인 필요",
    synced: "정상",
  };
  return labels[status] ?? status;
}

function positionStatus(symbol: SymbolSummary) {
  if (!symbol.open_position.is_open) {
    return {
      side: "없음",
      size: "-",
      exposure: "0 USDT",
      protection: "해당 없음",
      next: symbol.blocked_reasons.length > 0 ? translateReasonCode(symbol.blocked_reasons[0]) : "신규 진입 대기",
      tone: "neutral" as const,
    };
  }
  const quantity = symbol.open_position.quantity;
  const markPrice = symbol.open_position.mark_price;
  const exposure =
    quantity !== null && markPrice !== null ? formatMoney(Math.abs(quantity * markPrice), 2) : "-";
  const protection = symbol.protection_status.protected
    ? { label: "정상", tone: "safe" as const }
    : { label: "확인 필요", tone: "warn" as const };
  return {
    side: translateSide(symbol.open_position.side),
    size: quantity !== null ? formatNumber(Math.abs(quantity), 6) : "-",
    exposure,
    protection: protection.label,
    next: symbol.protection_status.protected ? "보호주문 유지" : "보호주문 확인",
    tone: protection.tone,
  };
}

function translateSide(value: string | null | undefined) {
  if (value === "long") return "롱";
  if (value === "short") return "숏";
  return value ?? "-";
}

function recentAuditEvents(operator: OperatorDashboardPayload) {
  const globalEvents = operator.audit_events;
  const symbolEvents = operator.symbols.flatMap((symbol) => symbol.audit_events);
  return [...globalEvents, ...symbolEvents]
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    .slice(0, 6);
}

function auditTone(event: AuditEvent): Tone {
  if (event.severity === "error" || event.severity === "critical") {
    return "danger";
  }
  if (event.severity === "warning") {
    return "warn";
  }
  return "safe";
}

function auditTitle(event: AuditEvent) {
  const message = event.message?.trim();
  return auditMessageTitleMap[message ?? ""] ?? auditTitleMap[event.event_type] ?? message ?? event.event_type.replace(/_/g, " ");
}

function auditDetail(event: AuditEvent) {
  const entityLabel: Record<string, string> = {
    scheduler_run: "자동 실행",
    binance: "거래소",
    settings: "설정",
    position: "포지션",
    order: "주문",
  };
  const label = entityLabel[event.entity_type] ?? event.entity_type;
  return event.entity_id ? `${label} ${event.entity_id}` : label;
}

function exposureCards(operator: OperatorDashboardPayload) {
  const exposure = operator.control.exposure_summary;
  const metrics = exposure.metrics && typeof exposure.metrics === "object" && !Array.isArray(exposure.metrics)
    ? (exposure.metrics as Record<string, unknown>)
    : {};
  const limits = exposure.limits && typeof exposure.limits === "object" && !Array.isArray(exposure.limits)
    ? (exposure.limits as Record<string, unknown>)
    : {};
  const headroom = exposure.headroom && typeof exposure.headroom === "object" && !Array.isArray(exposure.headroom)
    ? (exposure.headroom as Record<string, unknown>)
    : {};

  const grossPct = typeof metrics.gross_exposure_pct_equity === "number" ? metrics.gross_exposure_pct_equity : null;
  const dailyLossLimit = nestedNumber(operator.control.pnl_summary, ["max_daily_loss"]) ?? nestedNumber(limits, ["daily_loss"]);
  const headroomPct =
    typeof headroom.gross_exposure_pct === "number"
      ? headroom.gross_exposure_pct
      : typeof headroom.limiting_headroom_pct_equity === "number"
        ? headroom.limiting_headroom_pct_equity
        : null;

  return [
    {
      label: "총 노출",
      value: grossPct !== null ? formatPct(grossPct) : `${operator.control.open_positions}개 포지션`,
    },
    {
      label: "오늘 손익",
      value: formatMoney(operator.control.daily_pnl, 2),
    },
    {
      label: "보호 여유",
      value: headroomPct !== null ? formatPct(headroomPct) : dailyLossLimit !== null ? formatMoney(dailyLossLimit, 2) : "-",
    },
  ];
}

function toneClass(tone: Tone) {
  return {
    safe: "border-emerald-200 bg-emerald-50 text-emerald-800",
    warn: "border-amber-200 bg-amber-50 text-amber-900",
    danger: "border-rose-200 bg-rose-50 text-rose-800",
    neutral: "border-slate-200 bg-slate-50 text-slate-700",
    info: "border-blue-200 bg-blue-50 text-blue-800",
  }[tone];
}

function dotClass(tone: Tone) {
  return {
    safe: "bg-emerald-500",
    warn: "bg-amber-500",
    danger: "bg-rose-500",
    neutral: "bg-slate-400",
    info: "bg-blue-500",
  }[tone];
}

function Icon({
  name,
  className = "h-5 w-5",
}: {
  name: "shield" | "home" | "list" | "pie" | "clock" | "settings" | "bell" | "user" | "swap" | "pulse";
  className?: string;
}) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 2,
  };

  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      {name === "shield" ? <path {...common} d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6l-7-3Z" /> : null}
      {name === "shield" ? <path {...common} d="m9 12 2 2 4-5" /> : null}
      {name === "home" ? <path {...common} d="m4 11 8-7 8 7v9a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1v-9Z" /> : null}
      {name === "list" ? <path {...common} d="M9 7h11M9 12h11M9 17h11M4 7h.01M4 12h.01M4 17h.01" /> : null}
      {name === "pie" ? <path {...common} d="M11 3v9h9A9 9 0 1 1 11 3Z" /> : null}
      {name === "pie" ? <path {...common} d="M14 3.5A9 9 0 0 1 20.5 10H14V3.5Z" /> : null}
      {name === "clock" ? <circle {...common} cx="12" cy="12" r="9" /> : null}
      {name === "clock" ? <path {...common} d="M12 7v5l3 2" /> : null}
      {name === "settings" ? <path {...common} d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z" /> : null}
      {name === "settings" ? <path {...common} d="M4 12h2m12 0h2M12 4v2m0 12v2m5.7-13.7-1.4 1.4M7.7 16.3l-1.4 1.4m0-11.4 1.4 1.4m8.6 8.6 1.4 1.4" /> : null}
      {name === "bell" ? <path {...common} d="M18 16H6l1.5-2.5V10a4.5 4.5 0 0 1 9 0v3.5L18 16ZM10 19h4" /> : null}
      {name === "user" ? <circle {...common} cx="12" cy="8" r="3" /> : null}
      {name === "user" ? <path {...common} d="M5 20a7 7 0 0 1 14 0" /> : null}
      {name === "swap" ? <path {...common} d="M17 4l4 4-4 4M21 8H7m0 12-4-4 4-4M3 16h14" /> : null}
      {name === "pulse" ? <path {...common} d="M4 12h4l2-6 4 12 2-6h4" /> : null}
    </svg>
  );
}

function StatusPill({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-semibold ${toneClass(tone)}`}>
      <span className={`h-2 w-2 rounded-full ${dotClass(tone)}`} />
      {children}
    </span>
  );
}

function Panel({
  title,
  children,
  action,
  className = "",
}: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white p-6 shadow-sm ${className}`}>
      <div className="flex min-h-9 items-center justify-between gap-3">
        <h2 className="text-xl font-semibold tracking-[-0.01em] text-slate-950">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function TabButton({
  active,
  label,
  testId,
  onClick,
}: {
  active: boolean;
  label: string;
  testId: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className={`h-11 rounded-md border px-5 text-sm font-semibold transition focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
        active
          ? "border-blue-600 bg-blue-600 text-white"
          : "border-slate-200 bg-white text-slate-700 hover:border-blue-200 hover:bg-blue-50"
      }`}
    >
      {label}
    </button>
  );
}

function ProgressBar({ value }: { value: number }) {
  return (
    <div className="h-3 overflow-hidden rounded-md bg-slate-100" aria-label={`보호 여유 ${value}%`}>
      <div className="h-full rounded-md bg-emerald-500" style={{ width: `${value}%` }} />
    </div>
  );
}

async function fetchPayload(): Promise<OperatorDashboardPayload> {
  const response = await fetch(`${apiBaseUrl}/api/dashboard/operator`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || response.statusText);
  }
  return (await response.json()) as OperatorDashboardPayload;
}

export function OperatorFriendlyDashboard({ initial }: { initial: OperatorDashboardPayload }) {
  const [payload, setPayload] = useState(initial);
  const [lastUpdated, setLastUpdated] = useState(() =>
    new Date(initial.generated_at.endsWith("Z") ? initial.generated_at : `${initial.generated_at}Z`),
  );
  const [refreshError, setRefreshError] = useState("");
  const [activeTab, setActiveTab] = useState<TabId>("today");
  const [checkedIds, setCheckedIds] = useState<string[]>([]);
  const syncCatchUpNeeded = needsSyncCatchUp(payload.control);

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

  useEffect(() => {
    if (!syncCatchUpNeeded) {
      return;
    }

    let active = true;
    let attempts = 0;
    let timeoutId: number | null = null;

    const refreshUntilCaughtUp = () => {
      timeoutId = window.setTimeout(async () => {
        attempts += 1;
        try {
          const next = await fetchPayload();
          if (!active) {
            return;
          }
          setPayload(next);
          setLastUpdated(new Date());
          setRefreshError("");
          if (attempts < syncCatchUpMaxAttempts && needsSyncCatchUp(next.control)) {
            refreshUntilCaughtUp();
          }
        } catch (error) {
          if (!active) {
            return;
          }
          setRefreshError(error instanceof Error ? error.message : "대시보드 갱신에 실패했습니다.");
          if (attempts < syncCatchUpMaxAttempts) {
            refreshUntilCaughtUp();
          }
        }
      }, syncCatchUpIntervalMs);
    };

    refreshUntilCaughtUp();

    return () => {
      active = false;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [syncCatchUpNeeded]);

  const operator = payload;
  const state = mainState(operator);
  const protection = protectionLabel(operator.control);
  const sync = syncSummary(operator.control);
  const actions = useMemo(() => buildActionItems(operator), [operator]);
  const remainingActions = actions.filter((item) => !checkedIds.includes(item.id));
  const audits = recentAuditEvents(operator);
  const exposure = exposureCards(operator);
  const openPositionCount = operator.symbols.filter((symbol) => symbol.open_position.is_open).length;

  const toggleChecked = (id: string) => {
    setCheckedIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  };

  return (
    <div className="space-y-6">
          <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)] xl:items-center">
              <div className="flex min-w-0 items-center gap-5">
                <div className={`flex h-20 w-20 shrink-0 items-center justify-center rounded-full ${toneClass(state.tone)}`}>
                  <Icon name="shield" className="h-11 w-11" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-500">현재 거래 상태</p>
                  <h1
                    data-testid="operator-dashboard-title"
                    className={`mt-2 text-3xl font-semibold tracking-[-0.03em] sm:text-4xl ${
                      state.tone === "safe"
                        ? "text-emerald-700"
                        : state.tone === "danger"
                          ? "text-rose-700"
                          : "text-slate-950"
                    }`}
                  >
                    {state.title}
                  </h1>
                  <p className="mt-2 text-sm leading-6 text-slate-600">{state.detail}</p>
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-3">
                <div className="border-t border-slate-100 pt-4 sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0">
                  <div className="flex items-center gap-3 text-slate-500">
                    <Icon name="clock" />
                    <span className="text-sm">기준 시각</span>
                  </div>
                  <p className="mt-2 text-base font-semibold text-slate-950">{formatDateTime(operator.generated_at, { seconds: true })}</p>
                </div>
                <div className="border-t border-slate-100 pt-4 sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0">
                  <div className="flex items-center gap-3 text-slate-500">
                    <Icon name="pulse" />
                    <span className="text-sm">자동 실행</span>
                  </div>
                  <p className={`mt-2 text-base font-semibold ${operator.control.scheduler_status === "failed" ? "text-rose-700" : "text-emerald-700"}`}>
                    {operator.control.scheduler_status === "failed" ? "확인 필요" : "정상"}
                  </p>
                </div>
                <div className="border-t border-slate-100 pt-4 sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0">
                  <div className="flex items-center gap-3 text-slate-500">
                    <Icon name="swap" />
                    <span className="text-sm">상태 동기화</span>
                  </div>
                  <p className={`mt-2 text-base font-semibold ${sync.tone === "safe" ? "text-emerald-700" : sync.tone === "danger" ? "text-rose-700" : "text-amber-800"}`}>
                    {sync.label}
                  </p>
                </div>
              </div>
            </div>
          </section>

          <div className="grid gap-6 xl:grid-cols-[320px_minmax(330px,1fr)_360px]">
            <Panel title="안전 상태" className="xl:min-h-[510px]">
              <div className="mt-6 flex flex-col items-center text-center">
                <div className={`flex h-36 w-36 items-center justify-center rounded-full ${toneClass(protection.tone)}`}>
                  <Icon name="shield" className="h-20 w-20" />
                </div>
                <p className={`mt-5 text-2xl font-semibold ${protection.tone === "safe" ? "text-emerald-700" : "text-amber-800"}`}>
                  {protection.tone === "safe" ? "보호 장치 정상" : "보호 장치 확인 중"}
                </p>
                <p className="mt-2 text-sm text-slate-500">
                  {openPositionCount > 0 ? `${openPositionCount}개 포지션 기준` : "열린 포지션 없음"}
                </p>
              </div>
              <div className="mt-6 divide-y divide-slate-100 border-y border-slate-100">
                {[
                  {
                    label: "거래 일시정지",
                    value: operator.control.trading_paused ? "중지" : "해제",
                    tone: operator.control.trading_paused ? "danger" as const : "safe" as const,
                  },
                  {
                    label: "신규 진입",
                    value: operator.control.can_enter_new_position ? "허용" : "차단",
                    tone: operator.control.can_enter_new_position ? "safe" as const : state.tone,
                  },
                  { label: "보호주문 상태", value: protection.label, tone: protection.tone },
                  { label: "계좌/주문 동기화", value: sync.label, tone: sync.tone },
                ].map((row) => (
                  <div key={row.label} className="flex items-center justify-between gap-4 py-3">
                    <span className="flex items-center gap-3 text-sm text-slate-700">
                      <span className={`h-2.5 w-2.5 rounded-full ${dotClass(row.tone)}`} />
                      {row.label}
                    </span>
                    <span className={`rounded-md border px-2.5 py-1 text-sm font-semibold ${toneClass(row.tone)}`}>
                      {row.value}
                    </span>
                  </div>
                ))}
              </div>
            </Panel>

            <div className="space-y-6">
              <Panel
                title="확인 필요"
                action={
                  <span data-testid="remaining-actions" className="rounded-md bg-amber-100 px-3 py-1 text-sm font-semibold text-amber-900">
                    {remainingActions.length}
                  </span>
                }
              >
                <div className="mt-5 divide-y divide-slate-100 rounded-lg border border-amber-100">
                  {actions.length === 0 ? (
                    <div className="p-5">
                      <StatusPill tone="safe">0건</StatusPill>
                      <p className="mt-3 text-base font-semibold text-slate-950">바로 확인할 운영 항목이 없습니다.</p>
                      <p className="mt-1 text-sm leading-6 text-slate-600">단순 관망이나 조건 미충족은 이 목록에 올리지 않습니다.</p>
                    </div>
                  ) : (
                    actions.map((item) => {
                      const checked = checkedIds.includes(item.id);
                      return (
                        <div key={item.id} className="grid gap-4 p-4 sm:grid-cols-[1fr_auto] sm:items-center">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <StatusPill tone={item.tone}>{item.priority}</StatusPill>
                              <span className="text-sm font-medium text-slate-500">{item.symbol}</span>
                            </div>
                            <p className="mt-3 text-base font-semibold leading-6 text-slate-950">{item.title}</p>
                            <p className="mt-1 text-sm leading-6 text-slate-600">{item.detail}</p>
                          </div>
                          <button
                            type="button"
                            data-testid={`action-${item.id}`}
                            onClick={() => toggleChecked(item.id)}
                            className={`h-11 rounded-md border px-5 text-sm font-semibold transition focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
                              checked
                                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                                : "border-slate-300 bg-white text-slate-900 hover:bg-slate-50"
                            }`}
                          >
                            {checked ? "확인됨" : "확인하기"}
                          </button>
                        </div>
                      );
                    })
                  )}
                </div>
              </Panel>

              <Panel title="노출 및 보호 요약">
                <div className="mt-5 grid gap-4 sm:grid-cols-3">
                  {exposure.map((item) => (
                    <div key={item.label} className="rounded-md border border-slate-100 bg-slate-50 p-4">
                      <p className="text-sm text-slate-500">{item.label}</p>
                      <p className="mt-2 text-lg font-semibold text-slate-950">{item.value}</p>
                    </div>
                  ))}
                </div>
                <div className="mt-5 space-y-2">
                  <ProgressBar value={operator.control.can_enter_new_position ? 72 : 46} />
                  <p className="text-sm leading-6 text-slate-600">
                    보호주문, 동기화, 승인 상태를 통과해야 새 포지션을 열 수 있습니다.
                  </p>
                </div>
              </Panel>
            </div>

            <Panel title="최근 감사 로그" className="xl:min-h-[510px]">
              <ol className="mt-6 space-y-5">
                {audits.length === 0 ? (
                  <li className="rounded-lg border border-dashed border-slate-200 p-5 text-sm text-slate-500">
                    최근 감사 로그가 없습니다.
                  </li>
                ) : (
                    audits.map((event, index) => {
                      const tone = auditTone(event);
                      return (
                      <li key={`${event.event_type}-${event.entity_id}-${event.created_at}-${index}`} className="grid grid-cols-[64px_18px_1fr] gap-3">
                        <time className="pt-0.5 text-sm text-slate-500">{formatTime(event.created_at)}</time>
                        <span className={`mt-1.5 h-3 w-3 rounded-full ${dotClass(tone)}`} />
                        <div className="min-w-0 border-b border-slate-100 pb-4">
                          <p className="text-sm font-semibold leading-6 text-slate-950">{auditTitle(event)}</p>
                          <p className="mt-1 text-sm leading-6 text-slate-600">{auditDetail(event)}</p>
                        </div>
                      </li>
                    );
                  })
                )}
              </ol>
            </Panel>
          </div>

          <section id="today" className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap gap-2">
              {tabs.map((tab) => (
                <TabButton
                  key={tab.id}
                  active={activeTab === tab.id}
                  label={tab.label}
                  testId={`tab-${tab.id}`}
                  onClick={() => setActiveTab(tab.id)}
                />
              ))}
            </div>

            <div className="mt-4 border-t border-slate-100 pt-4">
              {activeTab === "today" ? (
                <div className="grid gap-4 lg:grid-cols-2">
                  {(actions.length > 0 ? actions : [{
                    id: "no-action",
                    title: "바로 확인할 항목 없음",
                    detail: "운영자가 지금 처리해야 할 안전 항목이 없습니다.",
                    symbol: "전체",
                    priority: "보통" as const,
                    tone: "safe" as const,
                  }]).map((item) => (
                    <div key={item.id} className="rounded-lg border border-slate-200 p-4">
                      <p className="text-sm font-medium text-slate-500">{item.symbol}</p>
                      <p className="mt-2 text-base font-semibold leading-6 text-slate-950">{item.title}</p>
                      <p className="mt-2 text-sm leading-6 text-slate-600">{item.detail}</p>
                    </div>
                  ))}
                </div>
              ) : null}

              {activeTab === "positions" ? (
                <div className="overflow-x-auto">
                  <table data-testid="position-table" className="min-w-full border-separate border-spacing-0 text-left text-sm">
                    <thead className="text-slate-500">
                      <tr>
                        <th className="whitespace-nowrap border-b border-slate-200 px-3 py-3 font-semibold">심볼</th>
                        <th className="whitespace-nowrap border-b border-slate-200 px-3 py-3 font-semibold">방향</th>
                        <th className="whitespace-nowrap border-b border-slate-200 px-3 py-3 font-semibold">수량</th>
                        <th className="whitespace-nowrap border-b border-slate-200 px-3 py-3 font-semibold">노출</th>
                        <th className="whitespace-nowrap border-b border-slate-200 px-3 py-3 font-semibold">보호</th>
                        <th className="whitespace-nowrap border-b border-slate-200 px-3 py-3 font-semibold">다음 조치</th>
                      </tr>
                    </thead>
                    <tbody>
                      {operator.symbols.map((symbol) => {
                        const row = positionStatus(symbol);
                        return (
                          <tr key={symbol.symbol}>
                            <td className="whitespace-nowrap border-b border-slate-100 px-3 py-3 font-semibold text-slate-950">{symbol.symbol}</td>
                            <td className="whitespace-nowrap border-b border-slate-100 px-3 py-3 text-slate-700">{row.side}</td>
                            <td className="whitespace-nowrap border-b border-slate-100 px-3 py-3 text-slate-700">{row.size}</td>
                            <td className="whitespace-nowrap border-b border-slate-100 px-3 py-3 text-slate-700">{row.exposure}</td>
                            <td className="whitespace-nowrap border-b border-slate-100 px-3 py-3">
                              <span className={`rounded-md border px-2.5 py-1 font-semibold ${toneClass(row.tone)}`}>
                                {row.protection}
                              </span>
                            </td>
                            <td className="whitespace-nowrap border-b border-slate-100 px-3 py-3 text-slate-700">{row.next}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {activeTab === "audit" ? (
                <div data-testid="audit-tab" className="divide-y divide-slate-100">
                  {audits.length === 0 ? (
                    <div className="py-6 text-sm text-slate-500">최근 감사 로그가 없습니다.</div>
                  ) : (
                    audits.map((event, index) => {
                      const tone = auditTone(event);
                      return (
                        <div key={`${event.event_type}-${event.entity_id}-${event.created_at}-${index}`} className="grid gap-3 py-4 sm:grid-cols-[72px_1fr_auto] sm:items-center">
                          <time className="text-sm text-slate-500">{formatTime(event.created_at)}</time>
                          <div>
                            <p className="text-sm font-semibold text-slate-950">{auditTitle(event)}</p>
                            <p className="mt-1 text-sm leading-6 text-slate-600">{auditDetail(event)}</p>
                          </div>
                          <span className={`w-fit rounded-md border px-2.5 py-1 text-sm font-semibold ${toneClass(tone)}`}>
                            {tone === "safe" ? "정상" : tone === "danger" ? "차단" : "확인"}
                          </span>
                        </div>
                      );
                    })
                  )}
                </div>
              ) : null}
            </div>
          </section>

          <div className="flex flex-wrap items-center justify-between gap-3 pb-8 text-sm text-slate-500">
            <span>마지막 로컬 갱신 {lastUpdated.toLocaleTimeString("ko-KR", { hour12: false, timeZone: "Asia/Seoul" })}</span>
            {refreshError ? <span className="text-rose-700">{refreshError}</span> : null}
          </div>
    </div>
  );
}
