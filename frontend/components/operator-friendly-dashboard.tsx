"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import type {
  EntryQualityBreakdown,
  ExchangeSyncDiagnostics,
  LimitedLiveReadiness,
  OperatorDashboardPayload,
  ProfitabilityCostBreakdown,
} from "./overview-dashboard";
import { formatAuditEntityType, formatAuditRowTitle } from "../lib/audit-log";
import {
  describeReasonCode,
  describeReasonCodeInContext,
  isEntryWaitReasonCodeInContext,
  isOperationalControlReasonCode,
  lookupRiskReasonCode,
} from "../lib/risk-reason-copy.js";
import { buildExecutionRiskProfileSummary } from "../lib/execution-risk-profile-summary";
import { normalizeSyncScopeStatus } from "../lib/sync-freshness";
import { handleOperatorApiAuthFailure, postJson, withOperatorWriteProtection } from "../lib/api";

type Tone = "safe" | "warn" | "danger" | "neutral" | "info";
type AuditEvent = OperatorDashboardPayload["audit_events"][number];

type ActionItem = {
  id: string;
  title: string;
  detail: string;
  symbol: string;
  priority: "높음" | "보통";
  tone: Tone;
  action?: "live-sync";
  buttonLabel?: string;
};

const apiBaseUrl = "";
const refreshIntervalMs = 60000;
const syncCatchUpIntervalMs = 2500;
const syncCatchUpMaxAttempts = 8;

const tradingSyncBlockers = new Set([
  "ACCOUNT_STATE_STALE",
  "POSITION_STATE_STALE",
  "OPEN_ORDERS_STATE_STALE",
  "PROTECTION_STATE_UNVERIFIED",
  "FULL_LIVE_SYNC_STALE",
]);

const recoverableSyncStatuses = new Set(["stale", "skipped", "unknown"]);
const entrySyncScopes = ["account", "positions", "open_orders", "protective_orders"] as const;

const reasonFallbackMap: Record<string, string> = {
  MANUAL_USER_REQUEST: "운영자가 수동으로 거래를 일시정지했습니다.",
  TRADING_PAUSED: "시스템 가드 모드로 신규 진입을 보류했습니다.",
  PROTECTIVE_ORDER_FAILURE: "보호주문 이상이 있어 신규 진입을 막고 있습니다.",
  MISSING_PROTECTIVE_ORDERS: "미보호 포지션이 있어 보호주문 확인이 필요합니다.",
  PROTECTION_REQUIRED: "보호주문 복구가 끝나기 전까지 신규 진입을 막습니다.",
  DEGRADED_MANAGE_ONLY: "관리 전용 상태라 신규 진입보다 보호와 정리를 우선합니다.",
  EMERGENCY_EXIT: "비상 청산 상태라 신규 진입을 막습니다.",
  HOLD_DECISION: "현재 AI 판단은 신규 진입 신호가 없어 대기 중입니다.",
  ENTRY_TRIGGER_NOT_MET: "현재 진입 조건이 아직 충족되지 않았습니다.",
  insufficient_sample: "실거래 표본 부족",
  productization_profitability_unverified: "제품화 수익성 미검증",
  negative_expectancy: "기대 수익 음수",
  excessive_drawdown: "최대 낙폭 초과",
  protection_failures: "보호 주문 실패 이력",
  execution_unknowns: "주문 제출 상태 미해결",
  stale_data_frequency_high: "지연/누락 데이터 차단 빈도 높음",
  ai_filter_underperforming: "AI 필터 실측 기여도 음수",
};

function unique(values: string[]) {
  return [...new Set(values.filter((value) => value.trim().length > 0))];
}

function translateReasonCode(value: string | null | undefined) {
  if (!value) {
    return "추가 사유 없음";
  }
  const copy = describeReasonCode(value);
  return copy.known ? copy.title_ko : lookupRiskReasonCode(value) ?? reasonFallbackMap[value] ?? value;
}

function translateReasonCodeInContext(value: string | null | undefined, allReasonCodes: string[] | null | undefined) {
  if (!value) {
    return "추가 사유 없음";
  }
  const copy = describeReasonCodeInContext(value, allReasonCodes);
  return copy.known ? copy.title_ko : lookupRiskReasonCode(value) ?? reasonFallbackMap[value] ?? value;
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

function currentControlBlockers(control: OperatorDashboardPayload["control"]) {
  const currentCycle = control.control_status_summary?.blocked_reasons_current_cycle ?? [];
  const explicitBlockers = control.control_status_summary?.blocked_reason_codes ?? control.blocked_reason_codes ?? [];
  const degraded = control.control_status_summary?.degraded_reason_codes ?? control.degraded_reason_codes ?? [];
  const protection = control.control_status_summary?.protection_reason_codes ?? control.protection_reason_codes ?? [];
  const approvalControl = control.control_status_summary?.approval_control_blocked_reasons ?? [];
  return unique([...explicitBlockers, ...currentCycle, ...degraded, ...protection, ...approvalControl]);
}

function hasActiveSyncProblem(control: OperatorDashboardPayload["control"]) {
  return entrySyncScopes.some((scope) => normalizeSyncScopeStatus(control.sync_freshness_summary[scope]) !== "synced");
}

function importantBlockers(control: OperatorDashboardPayload["control"]) {
  const blockers = currentControlBlockers(control);
  return blockers.filter((code) => {
    if (isEntryWaitReasonCodeInContext(code, blockers)) {
      return false;
    }
    if (tradingSyncBlockers.has(code) && !hasActiveSyncProblem(control) && control.unprotected_positions === 0) {
      return false;
    }
    return true;
  });
}

function currentRiskBlockers(control: OperatorDashboardPayload["control"]) {
  const currentCycle = control.control_status_summary?.blocked_reasons_current_cycle ?? [];
  const explicitBlockers = control.control_status_summary?.blocked_reason_codes ?? control.blocked_reason_codes ?? [];
  return unique([...currentCycle, ...explicitBlockers]);
}

function isCurrentRiskBlocked(control: OperatorDashboardPayload["control"]) {
  return control.control_status_summary?.risk_allowed === false;
}

function isGlobalEntryBlocked(control: OperatorDashboardPayload["control"]) {
  const summary = control.control_status_summary;
  const globalReasons = summary?.global_block_reason_codes ?? [];
  const blockScope = summary?.block_scope ?? "unknown";
  return globalReasons.length > 0 || (blockScope !== "none" && blockScope !== "candidate");
}

function currentOperationalBlockerCode(control: OperatorDashboardPayload["control"]) {
  if (control.can_enter_new_position) {
    return null;
  }
  const summary = control.control_status_summary;
  const candidates = unique([
    ...(summary?.global_block_reason_codes ?? []),
    ...(summary?.approval_control_blocked_reasons ?? []),
    control.guard_mode_reason_code ?? "",
  ]);
  return candidates.find((code) => isOperationalControlReasonCode(code)) ?? null;
}

function exchangePermissionUnknown(control: OperatorDashboardPayload["control"]) {
  const summary = control.control_status_summary;
  return (
    control.rollout_mode === "full_live" &&
    control.approval_armed &&
    summary?.exchange_can_trade_known === false
  );
}

function isPassiveRiskOnly(control: OperatorDashboardPayload["control"]) {
  const blockers = currentRiskBlockers(control);
  return blockers.length === 0 || blockers.every((code) => isEntryWaitReasonCodeInContext(code, blockers));
}

function hasStatusOnlyRiskBlocker(control: OperatorDashboardPayload["control"]) {
  const blockers = currentRiskBlockers(control);
  return blockers.some((code) => isEntryWaitReasonCodeInContext(code, blockers));
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

function needsExecutionProfileCatchUp(control: OperatorDashboardPayload["control"]) {
  return Boolean(control.profile_new_entry_blocked);
}

function schedulerSummary(control: OperatorDashboardPayload["control"]) {
  const freshness = control.scheduler_freshness_summary ?? {};
  if (freshness.stale === true || freshness.status === "stale") {
    return {
      label: "지연",
      detail: typeof freshness.message === "string" ? freshness.message : "최근 스케줄러 실행이 예정 시각보다 늦습니다.",
      tone: "danger" as const,
    };
  }
  if (control.scheduler_status === "failed") {
    return {
      label: "확인 필요",
      detail: "최근 스케줄러 실행이 실패했습니다.",
      tone: "danger" as const,
    };
  }
  if (freshness.status === "fresh") {
    return {
      label: "정상",
      detail: `최근 실행 ${formatDateTime(control.scheduler_last_run_at)}`,
      tone: "safe" as const,
    };
  }
  return {
    label: "미확인",
    detail: "스케줄러 실행 기록을 아직 확인하지 못했습니다.",
    tone: "warn" as const,
  };
}

function syncBlockerDetail(code: string) {
  const base = describeReasonCode(code).detail_ko;
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

function protectionHeading(control: OperatorDashboardPayload["control"]) {
  const protection = protectionLabel(control);
  if (protection.tone === "safe") {
    return "보호 장치 정상";
  }
  if (protection.tone === "neutral") {
    return "보호 대상 없음";
  }
  return "보호 장치 확인 중";
}

function mainState(operator: OperatorDashboardPayload) {
  const control = operator.control;
  const blockers = importantBlockers(control);
  const riskBlockers = currentRiskBlockers(control);
  const passiveRiskOnly = isPassiveRiskOnly(control);
  const operationalBlocker = currentOperationalBlockerCode(control);

  if (exchangePermissionUnknown(control)) {
    return {
      title: "거래소 권한 확인 중",
      detail: "full live 승인 상태지만 거래소 주문 가능 여부가 확인되지 않아 신규 진입을 보류합니다.",
      tone: "warn" as const,
    };
  }

  if (control.trading_paused) {
    const manualPause = control.pause_origin === "manual";
    return {
      title: manualPause ? "거래 일시정지" : "시스템 가드 모드",
      detail:
        translateReasonCode(control.pause_reason_code) ||
        (manualPause ? "운영자가 자동 거래를 일시정지했습니다." : "시스템 보호 조건으로 신규 진입을 보류했습니다."),
      tone: "danger" as const,
    };
  }

  if (operationalBlocker) {
    return {
      title: operationalBlocker === "LIVE_APPROVAL_REQUIRED" ? "실거래 승인 대기" : "운영 제어로 신규 진입 차단",
      detail: translateReasonCodeInContext(operationalBlocker, currentControlBlockers(control)),
      tone: "warn" as const,
    };
  }

  if (control.can_enter_new_position && !isGlobalEntryBlocked(control)) {
    return {
      title: "진입 안전 기준 정상",
      detail: isCurrentRiskBlocked(control)
        ? "최근 후보는 리스크 기준에서 보류됐지만, 전역 신규 진입 게이트는 열려 있습니다."
        : "계좌, 동기화, 보호주문 기준은 새 주문을 보낼 준비 상태입니다. 실제 신규 진입은 이번 AI/리스크 판단을 따릅니다.",
      tone: "safe" as const,
    };
  }

  if (isCurrentRiskBlocked(control)) {
    if (passiveRiskOnly) {
      const statusOnly = hasStatusOnlyRiskBlocker(control);
      return {
        title: statusOnly ? "신규 진입 대기" : "신규 진입 없음",
        detail: riskBlockers.length > 0 ? translateReasonCodeInContext(riskBlockers[0], riskBlockers) : "이번 판단 주기에서 신규 진입 신호가 없습니다.",
        tone: "neutral" as const,
      };
    }

    return {
      title: "신규 진입 차단",
      detail: riskBlockers.length > 0 ? translateReasonCodeInContext(riskBlockers[0], riskBlockers) : "이번 판단 주기에서 리스크 기준이 신규 진입을 막았습니다.",
      tone: "danger" as const,
    };
  }

  if (needsSyncCatchUp(control)) {
    return {
      title: "진입 안전 확인 중",
      detail: "잔고 화면은 최신이어도 신규 진입은 포지션, 미체결 주문, 보호주문 기준까지 다시 맞춘 뒤 허용됩니다.",
      tone: "warn" as const,
    };
  }

  if (control.can_enter_new_position) {
    return {
      title: "진입 안전 기준 정상",
      detail: "계좌, 동기화, 보호주문 기준은 새 주문을 보낼 준비 상태입니다. 실제 신규 진입은 이번 AI/리스크 판단을 따릅니다.",
      tone: "safe" as const,
    };
  }

  if (blockers.length === 0) {
    return {
      title: "신규 진입 없음",
      detail: "현재 조건에서는 신규 진입 신호를 기다리는 중입니다.",
      tone: "neutral" as const,
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
  const statuses = entrySyncScopes.map((key) => normalizeSyncScopeStatus(scopes[key]));
  if (statuses.some((status) => status === "failed" || status === "incomplete")) {
    return { label: "확인 필요", tone: "danger" as const };
  }
  if (statuses.some((status) => recoverableSyncStatuses.has(status))) {
    return { label: "갱신 중", tone: "warn" as const };
  }
  return { label: "정상", tone: "safe" as const };
}

function exchangeSyncDiagnostics(control: OperatorDashboardPayload["control"]): ExchangeSyncDiagnostics {
  return control.control_status_summary?.exchange_sync_diagnostics ?? control.exchange_sync_diagnostics ?? {};
}

function diagnosticCount(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function exchangeSyncPresentation(control: OperatorDashboardPayload["control"]) {
  const diagnostics = exchangeSyncDiagnostics(control);
  const status = diagnostics.status ?? "unknown";
  const blockState = diagnostics.current_block_state ?? "unknown";
  const failureCount = diagnosticCount(diagnostics.failure_count_24h);
  const permissionFailureCount = diagnosticCount(diagnostics.permission_failure_count_24h);
  const latestFailureAt = formatDateTime(diagnostics.latest_failure_at);
  const latestSuccessAt = formatDateTime(diagnostics.latest_success_at);
  const reason =
    diagnostics.latest_failure_reason_code === "EXCHANGE_AUTH_PERMISSION_REJECTED"
      ? "권한 거부"
      : diagnostics.latest_failure_reason_code ?? "실패";

  if (diagnostics.currently_blocking_new_entries || status === "blocked" || blockState === "currently_blocked") {
    return {
      label: "현재 차단",
      detail: `최근 실패 ${latestFailureAt} / ${reason} / 24시간 실패 ${failureCount}건, 권한 ${permissionFailureCount}건`,
      tone: "danger" as const,
    };
  }
  if (status === "recovered" || diagnostics.recovered_after_latest_failure) {
    return {
      label: "복구됨",
      detail: `최근 실패 이후 ${latestSuccessAt} 성공 / 권한 실패 이력 ${permissionFailureCount}건`,
      tone: "safe" as const,
    };
  }
  if (status === "healthy") {
    return {
      label: "정상",
      detail: `최근 성공 ${latestSuccessAt} / 24시간 실패 ${failureCount}건`,
      tone: "safe" as const,
    };
  }
  return {
    label: "확인 중",
    detail: "exchange_sync_cycle 실행 이력 또는 sync freshness 근거가 부족합니다.",
    tone: "neutral" as const,
  };
}

function entryPermissionStatus(control: OperatorDashboardPayload["control"]) {
  if (exchangePermissionUnknown(control)) {
    return { label: "권한 확인 중", tone: "warn" as const };
  }
  if (control.trading_paused) {
    return { label: "보류", tone: "neutral" as const };
  }
  const operationalBlocker = currentOperationalBlockerCode(control);
  if (operationalBlocker) {
    return {
      label: operationalBlocker === "LIVE_APPROVAL_REQUIRED" ? "승인 대기" : "운영 차단",
      tone: "warn" as const,
    };
  }
  if (control.can_enter_new_position && !isGlobalEntryBlocked(control)) {
    return { label: "준비됨", tone: "safe" as const };
  }
  if (isCurrentRiskBlocked(control)) {
    return isPassiveRiskOnly(control)
      ? { label: hasStatusOnlyRiskBlocker(control) ? "대기" : "진입 없음", tone: "neutral" as const }
      : { label: "차단", tone: "danger" as const };
  }
  if (needsSyncCatchUp(control)) {
    return { label: "갱신 중", tone: "warn" as const };
  }
  if (importantBlockers(control).length > 0) {
    return { label: "차단", tone: "danger" as const };
  }
  return control.can_enter_new_position
    ? { label: "준비됨", tone: "safe" as const }
    : { label: "보류", tone: "neutral" as const };
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

  if (control.stale_pending_entry_plan_count > 0) {
    const staleSymbols = unique(
      control.stale_pending_entry_plans
        .map((row) => (typeof row.symbol === "string" ? row.symbol : ""))
        .filter((value) => value.length > 0),
    );
    const stalePreview = staleSymbols.length > 0 ? ` (${staleSymbols.join(", ")})` : "";
    items.push({
      id: "stale-triggered-pending-plans",
      title: "만료된 triggered 진입 계획 정리 필요",
      detail: `DB에 만료된 triggered pending_entry_plans가 ${control.stale_pending_entry_plan_count}건 남아 있습니다${stalePreview}. 현재 오픈 포지션/오픈 주문과 분리된 stale history일 수 있어 watcher cleanup 결과를 확인해야 합니다.`,
      symbol: "전체",
      priority: "높음",
      tone: "warn",
    });
  }

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
    const canRefreshSync = code === "EXCHANGE_CAN_TRADE_UNKNOWN" || tradingSyncBlockers.has(code);
    items.push({
      id: `blocker-${code}`,
      title: code === "EXCHANGE_CAN_TRADE_UNKNOWN"
        ? "거래소 권한 확인 필요"
        : tradingSyncBlockers.has(code)
          ? "진입 안전 기준 갱신 중"
          : "신규 진입 차단 사유",
      detail: syncBlockerDetail(code),
      symbol: "전체",
      priority: ["PROTECTION_REQUIRED", ...tradingSyncBlockers].includes(code)
        ? "높음"
        : "보통",
      tone: "warn",
      action: canRefreshSync ? "live-sync" : undefined,
      buttonLabel: canRefreshSync ? "동기화" : undefined,
    });
  }

  for (const scope of entrySyncScopes) {
    const status = control.sync_freshness_summary[scope];
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
      action: "live-sync",
      buttonLabel: "동기화",
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

function recentAuditEvents(operator: OperatorDashboardPayload) {
  const globalEvents = operator.audit_events;
  const symbolEvents = operator.symbols.flatMap((symbol) => symbol.audit_events);
  const seen = new Set<string>();
  return [...globalEvents, ...symbolEvents]
    .filter((event) => {
      const key = [
        event.event_type,
        event.entity_type,
        event.entity_id ?? "",
        event.created_at,
        event.message ?? "",
      ].join("|");
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    })
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
  return formatAuditRowTitle(event);
}

function auditDetail(event: AuditEvent) {
  const label = formatAuditEntityType(event.entity_type);
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

const profitabilityWarningCopy: Record<string, string> = {
  fee_exceeds_gross_pnl: "수수료가 총손익보다 큽니다.",
  cost_exceeds_gross_pnl: "수수료와 펀딩비가 총손익보다 큽니다.",
  positive_gross_negative_net: "총손익은 양수지만 비용 반영 후 순손익은 음수입니다.",
  adverse_slippage_positive: "평균 체결 슬리피지가 거래자에게 불리하게 누적되고 있습니다.",
  high_marketable_ratio_low_net_pnl: "즉시체결형 진입 비중이 높고 순손익이 낮습니다.",
};

function costWindowLabel(value: string | null | undefined) {
  const labels: Record<string, string> = {
    today: "오늘",
    "24h": "24시간",
    "7d": "7일",
    "30d": "30일",
    all_time: "전체",
  };
  return value ? labels[value] ?? value : "-";
}

function entryQualityLabel(value: string) {
  const labels: Record<string, string> = {
    entry_passive_limit: "지정가 대기형",
    entry_marketable: "즉시체결형",
    entry_unknown: "분류 없음",
  };
  return labels[value] ?? value;
}

function primaryProfitabilityCost(operator: OperatorDashboardPayload): ProfitabilityCostBreakdown | null {
  const breakdowns = operator.market_signal.profitability_cost_breakdowns ?? [];
  return (
    breakdowns.find((item) => item.window_label === "today") ??
    operator.market_signal.performance_windows[0]?.cost_breakdown ??
    breakdowns[0] ??
    null
  );
}

function primaryEntryQuality(operator: OperatorDashboardPayload): EntryQualityBreakdown[] {
  const source = operator.market_signal.performance_windows[0]?.entry_quality ?? {};
  return ["entry_passive_limit", "entry_marketable", "entry_unknown"]
    .map((key) => source[key])
    .filter((item): item is EntryQualityBreakdown => Boolean(item));
}

function profitabilityTone(cost: ProfitabilityCostBreakdown | null): Tone {
  if (!cost || cost.status === "no_data") {
    return "neutral";
  }
  if (cost.warning_codes.includes("positive_gross_negative_net") || cost.net_pnl < 0) {
    return "danger";
  }
  return cost.warning_codes.length > 0 ? "warn" : "safe";
}

function readinessTone(readiness: LimitedLiveReadiness | null | undefined): Tone {
  if (!readiness) {
    return "neutral";
  }
  if (readiness.status === "blocked" || readiness.status === "not_ready") {
    return "danger";
  }
  if (readiness.status === "limited_live_candidate" || readiness.status === "scale_up_candidate") {
    return "safe";
  }
  return "warn";
}

function readinessLabel(readiness: LimitedLiveReadiness | null | undefined) {
  if (!readiness) {
    return "준비도 미확인";
  }
  if (
    readiness.status === "not_ready" &&
    readiness.reason_codes.includes("productization_profitability_unverified")
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
  return labels[readiness.status] ?? readiness.status;
}

function formatBps(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${formatNumber(value, 2)} bps`;
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

function feedbackTextClass(tone: Tone) {
  return {
    safe: "text-emerald-700",
    warn: "text-amber-700",
    danger: "text-rose-700",
    neutral: "text-slate-600",
    info: "text-blue-700",
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

function ProfitabilityCostPanel({
  cost,
  breakdowns,
  entryQuality,
  limitedLiveReadiness,
}: {
  cost: ProfitabilityCostBreakdown | null;
  breakdowns: ProfitabilityCostBreakdown[];
  entryQuality: EntryQualityBreakdown[];
  limitedLiveReadiness: LimitedLiveReadiness | null | undefined;
}) {
  const tone = profitabilityTone(cost);
  const readiness = limitedLiveReadiness;
  const readinessStatusTone = readinessTone(readiness);
  const lacksRealizedProfitabilityEvidence = readiness ? readiness.actual_entries === 0 || readiness.fills === 0 : false;
  const readinessReasonLabels = readiness?.reason_codes.map(translateReasonCode) ?? [];
  const warningLabels = cost?.warning_codes.map((code) => profitabilityWarningCopy[code] ?? code) ?? [];
  const statusLabel = !cost || cost.status === "no_data"
    ? "데이터 없음"
    : warningLabels.length > 0
      ? "비용 경고"
      : "정상";
  const visibleBreakdowns = breakdowns.length > 0 ? breakdowns : cost ? [cost] : [];
  const visibleEntryQuality = entryQuality.filter((item) => item.trade_count > 0);

  return (
    <Panel
      title="수익성 비용 분해"
      action={
        <div className="flex flex-wrap items-center gap-2">
            <Link
              href="/dashboard/analytics?section=cost"
              className="inline-flex min-h-9 items-center rounded-md border border-slate-300 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
            >
            기간별 보기
          </Link>
          <StatusPill tone={tone}>{statusLabel}</StatusPill>
        </div>
      }
    >
      <p className="mt-4 text-sm leading-6 text-slate-600">
        거래 확대 판단용 화면이 아니라 총손익이 수수료, 펀딩비, 불리한 체결에 줄어드는지 확인하는 화면입니다.
      </p>

      {readiness ? (
        <div className={`mt-4 rounded-md border p-4 text-sm ${toneClass(readinessStatusTone)}`}>
          <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
            <p className="font-semibold">{readinessLabel(readiness)}</p>
            <p>
              Net after fees {formatMoney(readiness.net_pnl_after_fees, 2)} / 기대값{" "}
              {formatMoney(readiness.expectancy_after_fees, 2)}
            </p>
          </div>
          <p className="mt-2 leading-6">
            실제 진입 {formatNumber(readiness.actual_entries)}건, 체결 {formatNumber(readiness.fills)}건, 섀도우 후보{" "}
            {formatNumber(readiness.recent_candidate_events)}건
            {readinessReasonLabels.length > 0 ? ` / ${readinessReasonLabels.join(", ")}` : ""}
          </p>
          {lacksRealizedProfitabilityEvidence ? (
            <p className="mt-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 font-semibold text-amber-900">
              판매 가능한 실현 수익성 증거 없음: 실제 주문/체결 0건 구간은 simulated opportunity와 분리해 판단해야 합니다.
            </p>
          ) : null}
        </div>
      ) : null}

      {cost ? (
        <>
          <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {[
              ["기간", costWindowLabel(cost.window_label), cost.status === "no_data" ? "집계 데이터 없음" : "비용 확인 기준"],
              ["순손익", formatMoney(cost.net_pnl, 2), `펀딩비 포함 ${formatMoney(cost.net_pnl_including_funding, 2)}`],
              ["총손익", formatMoney(cost.gross_pnl, 2), `실현 손익 ${formatMoney(cost.realized_pnl, 2)}`],
              ["수수료", formatMoney(cost.fee, 2), `총손익 대비 ${formatPct(cost.fee_to_gross_pnl_ratio)}`],
              ["펀딩비", formatMoney(cost.funding, 2), "양수는 수취, 음수는 비용"],
              ["총 비용", formatMoney(cost.total_cost, 2), `총손익 대비 ${formatPct(cost.cost_to_gross_pnl_ratio)}`],
              ["평균 슬리피지", formatBps(cost.signed_slippage_bps_avg), "양수는 불리한 평균 체결"],
              ["불리한 슬리피지", formatBps(cost.adverse_slippage_bps_avg), "불리한 방향만 누적한 평균"],
            ].map(([label, value, hint]) => (
              <div key={label} className="rounded-md border border-slate-100 bg-slate-50 p-4">
                <p className="text-sm text-slate-500">{label}</p>
                <p className="mt-2 text-lg font-semibold text-slate-950">{value}</p>
                <p className="mt-1 text-xs leading-5 text-slate-500">{hint}</p>
              </div>
            ))}
          </div>

          <div className="mt-5 grid gap-4 lg:grid-cols-2">
            <div className="rounded-md border border-slate-100 bg-slate-50 p-4">
              <p className="text-sm font-semibold text-slate-950">진입 방식 비중</p>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div>
                  <p className="text-xs text-slate-500">즉시체결형 진입</p>
                  <p className="mt-1 text-base font-semibold text-slate-950">
                    {formatPct(cost.marketable_entry_ratio)}
                  </p>
                  <p className="text-xs text-slate-500">{cost.marketable_entry_count}건</p>
                </div>
                <div>
                  <p className="text-xs text-slate-500">지정가 대기형 진입</p>
                  <p className="mt-1 text-base font-semibold text-slate-950">
                    {formatPct(cost.passive_entry_ratio)}
                  </p>
                  <p className="text-xs text-slate-500">{cost.passive_entry_count}건</p>
                </div>
              </div>
              <p className="mt-3 text-xs leading-5 text-slate-500">
                즉시체결형 비중이 높으면 빠른 체결 대신 수수료와 슬리피지 부담이 커질 수 있습니다.
              </p>
            </div>

            <div className={`rounded-md border p-4 ${toneClass(tone)}`}>
              <p className="text-sm font-semibold">비용 경고</p>
              {warningLabels.length > 0 ? (
                <ul className="mt-3 space-y-2 text-sm leading-6">
                  {warningLabels.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              ) : (
                <p className="mt-3 text-sm leading-6">현재 기간에서는 비용이 총손익을 넘는 경고가 없습니다.</p>
              )}
            </div>
          </div>

          {visibleEntryQuality.length > 0 ? (
            <div className="mt-5 rounded-md border border-slate-100 bg-white">
              <div className="grid gap-2 border-b border-slate-100 px-4 py-3 text-xs font-semibold text-slate-500 sm:grid-cols-6">
                <span>진입 방식</span>
                <span>거래</span>
                <span>승률</span>
                <span>순손익</span>
                <span>기대값</span>
                <span>슬리피지</span>
              </div>
              {visibleEntryQuality.map((item) => (
                <div
                  key={item.entry_type}
                  className="grid gap-2 border-b border-slate-100 px-4 py-3 text-sm last:border-b-0 sm:grid-cols-6"
                >
                  <span className="font-semibold text-slate-950">{entryQualityLabel(item.entry_type)}</span>
                  <span className="text-slate-700">{item.trade_count}건</span>
                  <span className="text-slate-700">{formatPct(item.win_rate)}</span>
                  <span className={item.net_pnl < 0 ? "font-semibold text-rose-700" : "text-slate-700"}>
                    {formatMoney(item.net_pnl, 2)}
                  </span>
                  <span className="text-slate-700">{formatMoney(item.expectancy, 2)}</span>
                  <span className="text-slate-700">
                    {formatBps(item.avg_signed_slippage_bps)} / 불리 {formatBps(item.avg_adverse_slippage_bps)}
                  </span>
                </div>
              ))}
            </div>
          ) : null}

          {visibleBreakdowns.length > 1 ? (
            <div className="mt-5 rounded-md border border-slate-100 bg-white">
              <div className="grid gap-2 border-b border-slate-100 px-4 py-3 text-xs font-semibold text-slate-500 sm:grid-cols-5">
                <span>기간</span>
                <span>순손익</span>
                <span>총손익</span>
                <span>총 비용</span>
                <span>진입 방식</span>
              </div>
              {visibleBreakdowns.map((item) => (
                <div
                  key={item.window_label}
                  className="grid gap-2 border-b border-slate-100 px-4 py-3 text-sm last:border-b-0 sm:grid-cols-5"
                >
                  <span className="font-semibold text-slate-950">{costWindowLabel(item.window_label)}</span>
                  <span className={item.net_pnl < 0 ? "font-semibold text-rose-700" : "text-slate-700"}>
                    {formatMoney(item.net_pnl, 2)}
                  </span>
                  <span className="text-slate-700">{formatMoney(item.gross_pnl, 2)}</span>
                  <span className="text-slate-700">{formatMoney(item.total_cost, 2)}</span>
                  <span className="text-slate-700">
                    즉시 {formatPct(item.marketable_entry_ratio)} / 대기 {formatPct(item.passive_entry_ratio)}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </>
      ) : (
        <div className="mt-5 rounded-md border border-dashed border-slate-200 bg-slate-50 p-5 text-sm text-slate-500">
          아직 비용 분해에 사용할 거래 데이터가 없습니다.
        </div>
      )}
    </Panel>
  );
}

async function fetchPayload(): Promise<OperatorDashboardPayload> {
  const response = await fetch(`${apiBaseUrl}/api/dashboard/operator?view=home`, { cache: "no-store" });
  if (!response.ok) {
    if (handleOperatorApiAuthFailure(response)) {
      throw new Error("운영자 세션이 만료되어 로그인 화면으로 이동합니다.");
    }
    const body = await response.text();
    throw new Error(body || response.statusText);
  }
  return (await response.json()) as OperatorDashboardPayload;
}

function shouldRefreshInBrowser() {
  return typeof document === "undefined" || document.visibilityState === "visible";
}

export function OperatorFriendlyDashboard({ initial }: { initial: OperatorDashboardPayload }) {
  const [payload, setPayload] = useState(initial);
  const [lastUpdated, setLastUpdated] = useState(() =>
    new Date(initial.generated_at.endsWith("Z") ? initial.generated_at : `${initial.generated_at}Z`),
  );
  const [refreshError, setRefreshError] = useState("");
  const [checkedIds, setCheckedIds] = useState<string[]>([]);
  const [pendingActionId, setPendingActionId] = useState<string | null>(null);
  const [actionFeedback, setActionFeedback] = useState<{
    id: string;
    tone: Tone;
    message: string;
  } | null>(null);
  const fastCatchUpNeeded = needsSyncCatchUp(payload.control) || needsExecutionProfileCatchUp(payload.control);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      if (!shouldRefreshInBrowser()) {
        return;
      }
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
    void refresh();
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void refresh();
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      active = false;
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  useEffect(() => {
    if (!fastCatchUpNeeded) {
      return;
    }

    let active = true;
    let attempts = 0;
    let timeoutId: number | null = null;

    const refreshUntilCaughtUp = () => {
      timeoutId = window.setTimeout(async () => {
        if (!shouldRefreshInBrowser()) {
          return;
        }
        attempts += 1;
        try {
          const next = await fetchPayload();
          if (!active) {
            return;
          }
          setPayload(next);
          setLastUpdated(new Date());
          setRefreshError("");
          if (
            attempts < syncCatchUpMaxAttempts &&
            (needsSyncCatchUp(next.control) || needsExecutionProfileCatchUp(next.control))
          ) {
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
  }, [fastCatchUpNeeded]);

  const operator = payload;
  const state = mainState(operator);
  const protection = protectionLabel(operator.control);
  const protectionTitle = protectionHeading(operator.control);
  const sync = syncSummary(operator.control);
  const scheduler = schedulerSummary(operator.control);
  const exchangeSync = exchangeSyncPresentation(operator.control);
  const entryPermission = entryPermissionStatus(operator.control);
  const actions = useMemo(() => buildActionItems(operator), [operator]);
  const remainingActions = actions.filter((item) => !checkedIds.includes(item.id));
  const audits = recentAuditEvents(operator);
  const exposure = exposureCards(operator);
  const profitabilityBreakdowns = operator.market_signal.profitability_cost_breakdowns ?? [];
  const profitabilityCost = primaryProfitabilityCost(operator);
  const entryQuality = primaryEntryQuality(operator);
  const openPositionCount =
    operator.control.open_positions ?? operator.symbols.filter((symbol) => symbol.open_position.is_open).length;
  const executionProfile = buildExecutionRiskProfileSummary(operator.control);

  const toggleChecked = (id: string) => {
    setCheckedIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  };

  const refreshPayload = async () => {
    const next = await fetchPayload();
    setPayload(next);
    setLastUpdated(new Date());
    setRefreshError("");
    return next;
  };

  const runLiveSyncAction = async (id: string) => {
    setPendingActionId(id);
    setActionFeedback({ id, tone: "warn", message: "거래소 권한과 동기화 상태를 확인 중입니다." });
    try {
      await postJson<Record<string, unknown>>(
        `/api/live/sync?symbol=${encodeURIComponent(payload.control.default_symbol)}&allow_protection_recovery=false`,
      );
      const next = await refreshPayload();
      const stillOpen = buildActionItems(next).some((item) => item.id === id);
      setActionFeedback({
        id,
        tone: stillOpen ? "warn" : "safe",
        message: stillOpen
          ? "동기화는 실행됐지만 이 안전 기준은 아직 해소되지 않았습니다."
          : "거래소 권한과 동기화 상태를 다시 확인했습니다.",
      });
    } catch (error) {
      setActionFeedback({
        id,
        tone: "danger",
        message: error instanceof Error ? error.message : "거래소 동기화에 실패했습니다.",
      });
    } finally {
      setPendingActionId(null);
    }
  };

  const handleActionClick = (item: ActionItem) => {
    if (item.action === "live-sync") {
      void runLiveSyncAction(item.id);
      return;
    }
    toggleChecked(item.id);
  };

  const handleLogout = async () => {
    const response = await fetch(
      "/api/operator/logout",
      await withOperatorWriteProtection("/api/operator/logout", { method: "POST", cache: "no-store" }),
    );
    if (!response.ok) {
      if (handleOperatorApiAuthFailure(response)) {
        return;
      }
      setRefreshError(`로그아웃 요청에 실패했습니다: ${response.status}`);
      return;
    }
    window.location.assign("/login");
  };

  return (
    <div className="space-y-6">
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => void handleLogout()}
              className="rounded border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-400 hover:text-slate-950"
            >
              로그아웃
            </button>
          </div>
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

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
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
                  <p className={`mt-2 text-base font-semibold ${scheduler.tone === "danger" ? "text-rose-700" : scheduler.tone === "warn" ? "text-amber-800" : "text-emerald-700"}`}>{scheduler.label}</p>
                  <p className="mt-1 text-xs leading-5 text-slate-500">{scheduler.detail}</p>
                  <p className="hidden">
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
                <div className="border-t border-slate-100 pt-4 sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0">
                  <div className="flex items-center gap-3 text-slate-500">
                    <Icon name="swap" />
                    <span className="text-sm">거래소 동기화</span>
                  </div>
                  <p className={`mt-2 text-base font-semibold ${exchangeSync.tone === "safe" ? "text-emerald-700" : exchangeSync.tone === "danger" ? "text-rose-700" : "text-slate-700"}`}>
                    {exchangeSync.label}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-slate-500">{exchangeSync.detail}</p>
                </div>
              </div>
            </div>
          </section>

          <div className="grid gap-6 xl:grid-cols-[minmax(280px,0.85fr)_minmax(0,1fr)] 2xl:grid-cols-[300px_minmax(0,1fr)_320px]">
            <Panel title="안전 상태" className="xl:min-h-[510px]">
              <div className="mt-6 flex flex-col items-center text-center">
                <div className={`flex h-36 w-36 items-center justify-center rounded-full ${toneClass(protection.tone)}`}>
                  <Icon name="shield" className="h-20 w-20" />
                </div>
                <p className={`mt-5 text-2xl font-semibold ${protection.tone === "safe" ? "text-emerald-700" : "text-amber-800"}`}>
                  {protectionTitle}
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
                    value: entryPermission.label,
                    tone: entryPermission.tone,
                  },
                  {
                    label: "실행 프로파일",
                    value: executionProfile.finalActiveProfileLabel,
                    tone: operator.control.profile_new_entry_blocked ? "danger" as const : "info" as const,
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
                      const pending = pendingActionId === item.id;
                      return (
                        <div key={item.id} className="grid gap-4 p-4 sm:grid-cols-[1fr_auto] sm:items-center">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <StatusPill tone={item.tone}>{item.priority}</StatusPill>
                              <span className="text-sm font-medium text-slate-500">{item.symbol}</span>
                            </div>
                            <p className="mt-3 text-base font-semibold leading-6 text-slate-950">{item.title}</p>
                            <p className="mt-1 text-sm leading-6 text-slate-600">{item.detail}</p>
                            {actionFeedback?.id === item.id ? (
                              <p className={`mt-2 text-sm font-medium ${feedbackTextClass(actionFeedback.tone)}`}>
                                {actionFeedback.message}
                              </p>
                            ) : null}
                          </div>
                          <button
                            type="button"
                            data-testid={`action-${item.id}`}
                            onClick={() => handleActionClick(item)}
                            disabled={pending}
                            className={`h-11 rounded-md border px-5 text-sm font-semibold transition focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
                              pending
                                ? "cursor-wait border-blue-200 bg-blue-50 text-blue-800"
                                : checked
                                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                                : "border-slate-300 bg-white text-slate-900 hover:bg-slate-50"
                            }`}
                          >
                            {pending ? "확인 중" : checked ? "확인됨" : item.buttonLabel ?? "확인하기"}
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
                <div className="mt-5 rounded-md border border-slate-100 bg-slate-50 px-4 py-3">
                  <p className="text-sm leading-6 text-slate-600">
                    위 수치는 백엔드가 계산한 최신 노출/여유 값입니다. 보호주문, 동기화, 승인 상태를 통과해야 새 포지션을 열 수 있습니다.
                  </p>
                </div>
              </Panel>

              <Panel title="AI 실행 리스크 프로파일">
                <div className="mt-5 grid gap-3 sm:grid-cols-3">
                  {[
                    ["기본 규칙", executionProfile.deterministicProfile, "규칙 기반 상태"],
                    ["AI 추천", executionProfile.aiRecommendedProfile, executionProfile.recommendationStatusLabel],
                    ["최종 적용", executionProfile.finalActiveProfile, executionProfile.profileBlockLabel],
                  ].map(([label, value, detail]) => (
                    <div key={label} className="rounded-md border border-slate-100 bg-slate-50 p-4">
                      <p className="text-sm text-slate-500">{label}</p>
                      <p className="mt-2 text-lg font-semibold text-slate-950">{value}</p>
                      <p className="mt-1 break-words text-xs text-slate-500">{detail}</p>
                    </div>
                  ))}
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <div className="rounded-md border border-slate-100 bg-slate-50 p-4">
                    <p className="text-sm text-slate-500">AI 추천 반영</p>
                    <p className="mt-2 text-base font-semibold text-slate-950">{executionProfile.applicationLabel}</p>
                    <p className="mt-1 break-words text-xs leading-5 text-slate-500">{executionProfile.applicationDetail}</p>
                  </div>
                  <div className="rounded-md border border-slate-100 bg-slate-50 p-4">
                    <p className="text-sm text-slate-500">진입/생존 경로</p>
                    <p className="mt-2 text-base font-semibold text-slate-950">
                      {executionProfile.newEntryLabel} / 생존 경로 {executionProfile.survivalPathLabel}
                    </p>
                    <p className="mt-1 text-xs leading-5 text-slate-500">{executionProfile.profileBlockDetail}</p>
                    <p className="mt-1 text-xs leading-5 text-slate-500">{executionProfile.survivalPathDetail}</p>
                  </div>
                </div>
                <div className="mt-4 rounded-md border border-slate-100 bg-slate-50 px-4 py-3">
                  <p className="text-sm leading-6 text-slate-600">
                    적용 방식 {executionProfile.selectionMode} / 추천 ID {executionProfile.recommendationId} /
                    신뢰도 {executionProfile.confidenceLabel} / 다음 검토 {formatDateTime(executionProfile.nextReviewAt)}
                  </p>
                  {executionProfile.isProfileSelectorShadow ? (
                    <p className="mt-2 text-sm leading-6 text-blue-800">
                      관찰 모드에서는 AI 추천을 기록만 하고 실제 프로파일이나 신규 진입 차단으로 적용하지 않습니다.
                    </p>
                  ) : null}
                  {executionProfile.ignoredReasonCodes.length > 0 ? (
                    <p className="mt-2 break-words text-sm leading-6 text-amber-800">
                      무시 사유 {executionProfile.ignoredReasonCodes.join(", ")}
                    </p>
                  ) : null}
                </div>
              </Panel>

              <ProfitabilityCostPanel
                cost={profitabilityCost}
                breakdowns={profitabilityBreakdowns}
                entryQuality={entryQuality}
                limitedLiveReadiness={payload.control.limited_live_readiness}
              />
            </div>

            <Panel title="최근 감사 로그" className="xl:col-span-2 xl:min-h-[360px] 2xl:col-span-1 2xl:min-h-[510px]">
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

          <div className="flex flex-wrap items-center justify-between gap-3 pb-8 text-sm text-slate-500">
            <span>마지막 로컬 갱신 {lastUpdated.toLocaleTimeString("ko-KR", { hour12: false, timeZone: "Asia/Seoul" })}</span>
            {refreshError ? <span className="text-rose-700">{refreshError}</span> : null}
          </div>
    </div>
  );
}
