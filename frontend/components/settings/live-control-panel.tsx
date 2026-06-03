"use client";

import { Field, InlineFeedback, StatusPill, Toggle, inputClass, type FeedbackMessage } from "./form-primitives";
import {
  describeReasonCodeInContext,
  isEntryWaitReasonCodeInContext,
  reasonCodeTitleInContext,
  type ReasonCodeCategory,
} from "../../lib/risk-reason-copy.js";
import { formatDisplayValue } from "../../lib/ui-copy";
import {
  resolveApprovalArmed,
  resolveApprovalExpiresAt,
  resolveUnlimitedApproval,
} from "../../lib/live-approval-state";
import { type AutoResumeAttemptResult, type ControlStatusSummary, type LiveSyncResult, type RolloutMode } from "./types";

type LiveControlState = {
  trading_paused: boolean;
  approval_armed?: boolean | null;
  approval_expires_at?: string | null;
  live_execution_armed: boolean;
  live_execution_ready: boolean;
  exchange_submit_allowed: boolean;
  live_approval_window_minutes: number;
  default_symbol: string;
  live_execution_armed_until: string | null;
  live_trading_env_enabled: boolean;
  operating_state: string;
  protection_recovery_status: string;
  pause_reason_code: string | null;
  pause_origin: string | null;
  auto_resume_after: string | null;
  auto_resume_eligible: boolean;
  auto_resume_status: string;
  auto_resume_last_blockers: string[];
  pause_recovery_class: string | null;
  guard_mode_reason_message: string | null;
};

type LiveControlForm = {
  rollout_mode: RolloutMode;
  live_approval_window_minutes: number;
  limited_live_max_notional: number | null;
  manual_live_approval: boolean;
};
const rolloutModeOptions: RolloutMode[] = ["paper", "shadow", "live_dry_run", "limited_live", "full_live"];

function rolloutModeLabel(mode: RolloutMode) {
  switch (mode) {
    case "paper":
      return "페이퍼";
    case "shadow":
      return "섀도";
    case "live_dry_run":
      return "실거래 드라이런";
    case "limited_live":
      return "제한 실거래";
    case "full_live":
      return "전체 실거래";
    default:
      return mode;
  }
}

function formatCodeList(values: string[] | null | undefined, empty = "-") {
  if (!values || values.length === 0) return empty;
  return values.map((item) => reasonCodeTitleInContext(item, values)).join(", ");
}

function approvalWindowLabel(minutes: number) {
  return minutes === 0 ? "무제한" : `${minutes}분`;
}

function approvalStateText(state: LiveControlState) {
  const approvalArmed = resolveApprovalArmed(state);
  const approvalExpiresAt = resolveApprovalExpiresAt(state);
  if (!approvalArmed) return "닫힘";
  if (approvalExpiresAt) {
    return `열림 (${formatDisplayValue(approvalExpiresAt, "live_execution_armed_until")})`;
  }
  return resolveUnlimitedApproval(state) ? "무제한 승인" : "열림";
}

function autoResumeAttemptText(result: AutoResumeAttemptResult) {
  const status = result.status ? formatDisplayValue(result.status) : "미확인";
  if (result.resumed) {
    return `복구 점검 통과: 시스템 가드가 해제되었습니다. 상태 ${status}`;
  }
  if (result.blockers && result.blockers.length > 0) {
    return `복구 점검 차단: ${formatCodeList(result.blockers)}`;
  }
  return `복구 점검 결과: ${status}`;
}

function AutoResumeAttemptPanel({ result }: { result: AutoResumeAttemptResult | null }) {
  if (!result) return null;
  const tone = result.resumed || result.status === "ready" ? "good" : result.status === "not_eligible" ? "neutral" : "warn";
  return (
    <div className="mt-3 rounded-md border border-slate-200 bg-white px-4 py-3 text-sm leading-6 text-slate-700">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill tone={tone}>{formatDisplayValue(result.status ?? "unknown")}</StatusPill>
        {result.reason_code ? <StatusPill tone="neutral">{formatDisplayValue(result.reason_code)}</StatusPill> : null}
      </div>
      <p className="mt-2">{autoResumeAttemptText(result)}</p>
      {result.symbol_blockers && Object.keys(result.symbol_blockers).length > 0 ? (
        <p className="mt-2 text-xs text-slate-500">
          심볼별 차단:{" "}
          {Object.entries(result.symbol_blockers)
            .map(([symbol, blockers]) => `${symbol}: ${formatCodeList(blockers)}`)
            .join(" / ")}
        </p>
      ) : null}
    </div>
  );
}

function renderMissingProtectionItems(
  missingItems: Record<string, string[]> | null | undefined,
  empty = "누락된 보호 항목 없음",
) {
  const entries = Object.entries(missingItems ?? {}).filter(([, values]) => values.length > 0);
  if (entries.length === 0) return empty;
  return entries
    .map(([symbol, values]) => `${symbol}: ${values.map((item) => formatDisplayValue(item)).join(", ")}`)
    .join(" / ");
}

function reasonTone(category: ReasonCodeCategory) {
  if (category === "operational_control") return "danger" as const;
  if (category === "safety_block") return "danger" as const;
  if (category === "entry_wait") return "warn" as const;
  return "neutral" as const;
}

function reasonCardClass(category: ReasonCodeCategory) {
  if (category === "operational_control" || category === "safety_block") {
    return "border-rose-100 bg-rose-50 text-rose-950";
  }
  if (category === "entry_wait") {
    return "border-amber-100 bg-amber-50 text-amber-950";
  }
  return "border-slate-200 bg-slate-50 text-slate-800";
}

function reasonSummaryText(reason: string | null | undefined, allReasonCodes?: string[] | null) {
  const copy = describeReasonCodeInContext(reason, allReasonCodes);
  return `${copy.title_ko} ${copy.auto_clear_hint_ko}`;
}

function ReasonExplanationCard({ reason, allReasonCodes }: { reason: string; allReasonCodes?: string[] | null }) {
  const copy = describeReasonCodeInContext(reason, allReasonCodes);
  return (
    <div className={`rounded-2xl border px-4 py-3 text-sm leading-6 ${reasonCardClass(copy.category)}`}>
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-semibold">{copy.title_ko}</p>
        <StatusPill tone={reasonTone(copy.category)}>
          {copy.category === "entry_wait"
            ? "진입 대기"
            : copy.category === "operational_control"
              ? "운영 제어"
              : copy.category === "safety_block"
                ? "안전 차단"
                : "확인 필요"}
        </StatusPill>
      </div>
      <p className="mt-1">{copy.detail_ko}</p>
      <p className="mt-2 text-xs text-slate-600">자동 해소: {copy.auto_clear_hint_ko}</p>
      <p className="mt-1 text-xs text-slate-600">확인 위치: {copy.check_location_ko}</p>
      <p className="mt-1 break-all font-mono text-[11px] text-slate-500">raw: {copy.raw_code || "-"}</p>
    </div>
  );
}

function ControlStatusPanel({
  state,
  summary,
}: {
  state: LiveControlState;
  summary: ControlStatusSummary;
}) {
  const currentCycleBlockedReasons = summary.blocked_reasons_current_cycle;
  const approvalExpiresAt = resolveApprovalExpiresAt(state);
  const approvalBlockedReasons = summary.approval_control_blocked_reasons ?? [];
  const approvalReasonContext = [...approvalBlockedReasons, ...currentCycleBlockedReasons, ...(summary.blocked_reason_codes ?? [])];
  const hardApprovalBlockedReasons = approvalBlockedReasons.filter(
    (reason) => !isEntryWaitReasonCodeInContext(reason, approvalReasonContext),
  );
  const waitingApprovalReasons = approvalBlockedReasons.filter((reason) =>
    isEntryWaitReasonCodeInContext(reason, approvalReasonContext),
  );
  const primaryBlocker = currentCycleBlockedReasons[0];
  const primaryBlockerIsEntryWait = isEntryWaitReasonCodeInContext(primaryBlocker, currentCycleBlockedReasons);
  const cards = [
    {
      label: "운영 모드",
      value: rolloutModeLabel(summary.rollout_mode),
      detail:
        summary.rollout_mode === "paper"
          ? "페이퍼 경로만 사용하고 거래소 주문 제출은 비활성화됩니다."
          : summary.rollout_mode === "shadow"
            ? "AI / 리스크 / 실행 intent와 감사 로그까지만 수행하고 실제 주문 제출은 금지됩니다."
            : summary.rollout_mode === "live_dry_run"
              ? "거래소 동기화와 사전 점검까지만 수행하고 실제 주문 제출은 금지됩니다."
              : summary.rollout_mode === "limited_live"
                ? `실제 주문 제출은 허용되지만 주문당 notional이 ${formatDisplayValue(summary.limited_live_max_notional, "limited_live_max_notional")}로 제한됩니다.`
                : "전체 실거래 주문 제출 경로를 사용합니다.",
      tone:
        summary.rollout_mode === "full_live"
          ? ("good" as const)
          : summary.rollout_mode === "limited_live"
            ? ("warn" as const)
            : ("neutral" as const),
    },
    {
      label: "앱 실거래 승인",
      value: summary.app_live_armed ? "승인됨" : "승인 닫힘",
      detail: summary.app_live_armed
        ? "앱 실거래 경로가 승인된 상태입니다."
        : "앱 실거래 승인이 닫혀 있어 실거래 경로가 열리지 않습니다.",
      tone: summary.app_live_armed ? ("good" as const) : ("warn" as const),
    },
    {
      label: "실거래 승인 창",
      value: summary.approval_window_open ? "열림" : "닫힘",
      detail: summary.approval_window_open
        ? approvalExpiresAt
          ? `만료 ${formatDisplayValue(approvalExpiresAt, "live_execution_armed_until")}`
          : resolveUnlimitedApproval(state)
            ? "무제한 승인 상태입니다."
            : "실거래 승인 창이 현재 유효합니다."
        : "신규 진입 전 실거래 승인 창을 다시 열어야 합니다.",
      tone: summary.approval_window_open ? ("good" as const) : ("warn" as const),
    },
    {
      label: "운영 중지",
      value: summary.paused ? "중지" : "운영 중",
      detail: summary.paused
        ? formatDisplayValue(state.pause_reason_code, "pause_reason_code")
        : "운영 중지 플래그가 활성화되어 있지 않습니다.",
      tone: summary.paused ? ("danger" as const) : ("good" as const),
    },
    {
      label: "안전 모드",
      value: summary.degraded ? "관리 전용" : "정상",
      detail: summary.degraded
        ? `${formatDisplayValue(state.operating_state, "operating_state")} / 보호 복구 ${formatDisplayValue(state.protection_recovery_status, "protection_recovery_status")}`
        : "관리 전용 또는 비상 복구 상태로 내려가 있지 않습니다.",
      tone: summary.degraded ? ("warn" as const) : ("good" as const),
    },
    {
      label: "신규 진입 리스크",
      value:
        summary.risk_allowed === null
          ? "미평가"
          : summary.risk_allowed
            ? "허용"
            : primaryBlockerIsEntryWait
              ? "대기"
              : "차단",
      detail:
        summary.risk_allowed === null
          ? "이번 판단 주기 리스크 결과가 아직 집계되지 않았습니다."
          : summary.risk_allowed
            ? "이번 판단 주기 리스크 가드가 신규 진입을 허용했습니다."
            : primaryBlockerIsEntryWait
              ? reasonSummaryText(primaryBlocker, currentCycleBlockedReasons)
            : primaryBlocker
              ? reasonSummaryText(primaryBlocker, currentCycleBlockedReasons)
              : state.guard_mode_reason_message ?? "이번 판단 주기 리스크 가드가 신규 진입을 차단했습니다.",
      tone:
        summary.risk_allowed === null
          ? ("neutral" as const)
          : summary.risk_allowed
            ? ("good" as const)
            : primaryBlockerIsEntryWait
              ? ("warn" as const)
              : ("danger" as const),
    },
  ];

  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-3 md:grid-cols-2">
        {cards.map((card) => (
          <div key={card.label} className="rounded-md border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-medium text-slate-500">{card.label}</p>
              <StatusPill tone={card.tone}>{card.value}</StatusPill>
            </div>
            <p className="mt-3 text-sm leading-6 text-slate-700">{card.detail}</p>
          </div>
        ))}
      </div>

      <div className="rounded-md border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-slate-900">이번 판단 주기 상태</p>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              이번 판단 주기에서 신규 진입을 진행하지 않은 이유를 보여줍니다. 운영 중지나 실거래 승인 차단과는 별도로 봅니다.
            </p>
          </div>
          <StatusPill tone={currentCycleBlockedReasons.length > 0 ? "warn" : "good"}>
            {currentCycleBlockedReasons.length > 0 ? `${currentCycleBlockedReasons.length}건` : "대기 사유 없음"}
          </StatusPill>
        </div>
        <div className="mt-4 space-y-2">
          {currentCycleBlockedReasons.length === 0 ? (
            <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
              이번 판단 주기 기준 대기/차단 사유는 없습니다.
            </div>
          ) : (
            currentCycleBlockedReasons.map((reason) => (
              <ReasonExplanationCard key={reason} reason={reason} allReasonCodes={currentCycleBlockedReasons} />
            ))
          )}
        </div>
        {state.auto_resume_last_blockers.length > 0 ? (
          <div className="mt-4 rounded-2xl border border-dashed border-slate-200 px-4 py-3 text-sm text-slate-600">
            자동 복구 차단 사유: {formatCodeList(state.auto_resume_last_blockers)}
          </div>
        ) : null}
      </div>

      <div className="rounded-md border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-slate-900">승인/운영 제어 요약</p>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              승인 창, 운영 중지, 실거래 경로 문제를 분리하고, 단순 대기 사유는 낮은 우선순위로 보여줍니다.
            </p>
          </div>
          <StatusPill
            tone={
              hardApprovalBlockedReasons.length > 0
                ? "danger"
                : "good"
            }
          >
            {hardApprovalBlockedReasons.length > 0
              ? `${hardApprovalBlockedReasons.length}건`
              : "정상"}
          </StatusPill>
        </div>
        <div className="mt-4 space-y-2">
          {hardApprovalBlockedReasons.length === 0 ? (
            <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
              승인/운영 제어 관점에서 즉시 차단 사유가 없습니다.
            </div>
          ) : (
            hardApprovalBlockedReasons.map((reason) => (
              <ReasonExplanationCard key={reason} reason={reason} allReasonCodes={approvalReasonContext} />
            ))
          )}
          {waitingApprovalReasons.length > 0 ? (
            <div className="rounded-2xl border border-dashed border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-950">
              <p className="font-semibold">진입 대기 사유 {waitingApprovalReasons.length}건은 운영 중지나 승인 차단이 아닙니다.</p>
              <p className="mt-1 text-xs text-slate-600">
                {waitingApprovalReasons.map((reason) => `${reasonCodeTitleInContext(reason, approvalReasonContext)} (raw: ${reason})`).join(" / ")}
              </p>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function LiveSyncPanel({ result }: { result: LiveSyncResult | null }) {
  if (!result) return null;
  const protectionEntries = Object.entries(result.symbol_protection_state ?? {});
  const missingProtectionText = renderMissingProtectionItems(result.missing_protection_items);
  const hasProtectionIssues =
    (result.unprotected_positions?.length ?? 0) > 0 ||
    (result.missing_protection_symbols?.length ?? 0) > 0 ||
    protectionEntries.some(([, state]) => !state.protected);

  return (
    <div className="mt-3 space-y-3 rounded-md border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap gap-2">
        <StatusPill>동기화 심볼 {result.symbols?.join(", ") ?? "-"}</StatusPill>
        <StatusPill>주문 {result.synced_orders ?? 0}</StatusPill>
        <StatusPill>포지션 {result.synced_positions ?? 0}</StatusPill>
        {typeof result.equity === "number" ? (
          <StatusPill>자산 {formatDisplayValue(result.equity, "equity")}</StatusPill>
        ) : null}
      </div>
      <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-600">
        이 결과는 방금 실행한 거래소 동기화와 보호 주문 확인 결과입니다. 실거래 준비 상태, 운영 중지, 가드 모드,
        차단 사유 해석은 개요 화면을 기준으로 확인합니다.
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div
          className={`rounded-2xl px-4 py-3 ${
            hasProtectionIssues ? "border border-rose-200 bg-rose-50" : "border border-emerald-200 bg-emerald-50"
          }`}
        >
          <p className="text-xs text-slate-500">보호 확인 결과</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {hasProtectionIssues
              ? "미보호 항목이 있어 보호 조치 확인이 필요합니다."
              : "포지션과 보호 주문 기준으로 추가 조치가 필요하지 않습니다."}
          </p>
        </div>
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="text-xs text-slate-500">누락 보호 항목</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{missingProtectionText}</p>
        </div>
      </div>
      {protectionEntries.length > 0 ? (
        <div className="grid gap-3 md:grid-cols-2">
          {protectionEntries.map(([symbol, state]) => (
            <div key={symbol} className="rounded-md bg-slate-50 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill>{symbol}</StatusPill>
                <StatusPill tone={state.protected ? "good" : "danger"}>
                  {state.protected ? "보호됨" : "보호 필요"}
                </StatusPill>
              </div>
              <p className="mt-3 text-sm text-slate-700">
                상태 {formatDisplayValue(state.status, "status")} / 보호 주문 {state.protective_order_count ?? 0}개
              </p>
              <p className="mt-2 text-sm text-slate-600">
                손절 {formatDisplayValue(state.has_stop_loss, "has_stop_loss")} / 익절{" "}
                {formatDisplayValue(state.has_take_profit, "has_take_profit")}
              </p>
              {!state.protected && (state.missing_components?.length ?? 0) > 0 ? (
                <p className="mt-2 text-sm text-rose-700">누락: {state.missing_components?.join(", ")}</p>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-amber-300 px-4 py-5 text-sm text-slate-500">
          이번 동기화 응답에는 심볼별 보호 상태가 포함되지 않았습니다.
        </div>
      )}
      {(result.unprotected_positions?.length ?? 0) > 0 ? (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          미보호 포지션 감지: {result.unprotected_positions?.join(", ")}
        </div>
      ) : null}
      {(result.emergency_actions_taken?.length ?? 0) > 0 ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <p className="font-semibold">비상 조치 발생</p>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs">
            {JSON.stringify(result.emergency_actions_taken, null, 2)}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

export function LiveControlPanel({
  state,
  summary,
  form,
  liveArmBlocked,
  liveArmDisableReason,
  actionsUseSavedSettings,
  feedback,
  liveSyncResult,
  resumeAttemptResult,
  onPause,
  onResume,
  onAttemptResume,
  onArm,
  onArmUnlimited,
  onDisarm,
  onSync,
  onFieldChange,
}: {
  state: LiveControlState;
  summary: ControlStatusSummary;
  form: LiveControlForm;
  liveArmBlocked: boolean;
  liveArmDisableReason: string | null | undefined;
  actionsUseSavedSettings: boolean;
  feedback?: FeedbackMessage;
  liveSyncResult: LiveSyncResult | null;
  resumeAttemptResult: AutoResumeAttemptResult | null;
  onPause: () => void;
  onResume: () => void;
  onAttemptResume: () => void;
  onArm: () => void;
  onArmUnlimited: () => void;
  onDisarm: () => void;
  onSync: () => void;
  onFieldChange: (field: keyof LiveControlForm, value: LiveControlForm[keyof LiveControlForm]) => void;
}) {
  const systemPause = state.trading_paused && state.pause_origin !== "manual";
  const approvalArmed = resolveApprovalArmed(state);
  const approvalExpiresAt = resolveApprovalExpiresAt(state);
  return (
    <section className="rounded-lg border border-slate-200 bg-slate-50 p-4 sm:p-5">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div>
          <h3 className="text-lg font-semibold text-slate-900">실거래 제어</h3>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
            운영 중지, 승인 창 제어, 거래소 재동기화처럼 즉시 반응이 필요한 제어를 상단 핵심 패널로 모았습니다.
            아래 상태는 백엔드가 내려준 현재 차단/승인 요약이며, 심볼별 세부 흐름은 개요 화면에서 확인합니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill tone={state.trading_paused ? "danger" : "good"}>
            {state.trading_paused ? "운영 중지" : "운영 중"}
          </StatusPill>
          <StatusPill tone={approvalArmed ? "good" : "warn"}>
            {approvalArmed
              ? approvalExpiresAt
                ? "승인 창 열림"
                : resolveUnlimitedApproval(state)
                  ? "무제한 승인"
                  : "승인 창 열림"
              : "승인 창 닫힘"}
          </StatusPill>
          <StatusPill tone={state.live_execution_ready ? "good" : "warn"}>
            {state.live_execution_ready ? "실거래 경로 준비" : "실거래 경로 제한"}
          </StatusPill>
          <StatusPill tone={state.exchange_submit_allowed ? "good" : "neutral"}>
            {state.exchange_submit_allowed ? "실주문 제출 허용" : "실주문 제출 제한"}
          </StatusPill>
        </div>
      </div>

      <ControlStatusPanel state={state} summary={summary} />

      <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-slate-900">즉시 실행</p>
              <p className="mt-1 text-sm leading-6 text-slate-600">
                운영 중지, 실거래 승인, 거래소 동기화는 저장된 현재 설정 기준으로만 실행합니다.
              </p>
            </div>
            <StatusPill tone="neutral">저장값 기준 실행</StatusPill>
          </div>
          <p className="mt-4 text-sm leading-6 text-slate-600">
            즉시 중지는 신규 진입만 막는 운영 중지입니다. 기존 포지션의 보호 주문 유지, 축소, 비상 청산은 계속 허용됩니다.
          </p>
          <div className="mt-4 grid gap-3 md:grid-cols-3">
            <div className="rounded-md border border-rose-100 bg-rose-50 p-3">
              <p className="text-xs font-semibold text-rose-900">운영 중지</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button className="rounded-md bg-rose-600 px-4 py-2 text-sm font-semibold text-white" onClick={onPause} type="button">
                  즉시 중지
                </button>
                {systemPause ? (
                  <button className="rounded-md bg-slate-950 px-4 py-2 text-sm font-semibold text-white" onClick={onAttemptResume} type="button">
                    복구 점검 후 해제
                  </button>
                ) : null}
                <button className="rounded-md border border-rose-200 bg-white px-4 py-2 text-sm font-semibold text-rose-900" onClick={onResume} type="button">
                  {systemPause ? "수동 해제" : "중지 해제"}
                </button>
              </div>
              {systemPause ? (
                <p className="mt-3 text-xs leading-5 text-rose-900">
                  자동 복구 대상 {formatDisplayValue(state.auto_resume_eligible, "auto_resume_eligible")} / 상태{" "}
                  {formatDisplayValue(state.auto_resume_status, "auto_resume_status")}
                  {state.auto_resume_after ? ` / 예정 ${formatDisplayValue(state.auto_resume_after, "auto_resume_after")}` : ""}
                </p>
              ) : null}
            </div>
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
              <p className="text-xs font-semibold text-slate-900">실거래 승인</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  className="rounded-md bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-400"
                  disabled={liveArmBlocked}
                  onClick={onArm}
                  type="button"
                >
                  승인 열기
                </button>
                <button
                  className="rounded-md bg-emerald-700 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-400"
                  disabled={liveArmBlocked}
                  onClick={onArmUnlimited}
                  type="button"
                >
                  무제한 승인
                </button>
                <button className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700" onClick={onDisarm} type="button">
                  승인 닫기
                </button>
              </div>
            </div>
            <div className="rounded-md border border-amber-100 bg-amber-50 p-3">
              <p className="text-xs font-semibold text-amber-900">거래소 확인</p>
              <button className="mt-3 rounded-md border border-amber-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700" onClick={onSync} type="button">
                거래소 동기화
              </button>
            </div>
          </div>
          <p className="mt-3 text-xs leading-5 text-slate-500">
            실거래 승인 시간: 저장값 {approvalWindowLabel(state.live_approval_window_minutes)} / 동기화 심볼: 저장값 {state.default_symbol}
            {actionsUseSavedSettings ? " / 현재 입력값과 저장값이 다르면 저장 후 다시 실행하세요." : ""}
          </p>
          <div className="mt-3">
            <InlineFeedback message={feedback} />
          </div>
          <AutoResumeAttemptPanel result={resumeAttemptResult} />
          {liveArmBlocked && liveArmDisableReason ? (
            <p className="mt-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
              실거래 승인 버튼 비활성화 사유: {liveArmDisableReason}
            </p>
          ) : null}
          <LiveSyncPanel result={liveSyncResult} />
        </div>

        <div className="rounded-md border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-slate-900">저장형 운영 기본값</p>
              <p className="mt-1 text-sm leading-6 text-slate-600">
                운영 모드, 승인 유지 시간, 제한 실거래 주문 한도는 저장 후에만 런타임에 반영됩니다.
              </p>
            </div>
            <StatusPill tone="neutral">저장 후 반영</StatusPill>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Field label="운영 모드">
              <select
                className={inputClass}
                value={form.rollout_mode}
                onChange={(event) => onFieldChange("rollout_mode", event.target.value as RolloutMode)}
              >
                {rolloutModeOptions.map((option) => (
                  <option key={option} value={option}>
                    {rolloutModeLabel(option)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="승인 유지 시간(분)">
              <input
                className={inputClass}
                min={0}
                max={240}
                type="number"
                value={form.live_approval_window_minutes}
                onChange={(event) => onFieldChange("live_approval_window_minutes", Number(event.target.value))}
              />
              <p className="mt-2 text-xs leading-5 text-slate-500">0은 무제한 승인으로 저장됩니다.</p>
            </Field>
            <Field label="제한 실거래 주문당 최대 금액">
              <input
                className={inputClass}
                min={1}
                step="1"
                type="number"
                value={form.limited_live_max_notional ?? 500}
                onChange={(event) => onFieldChange("limited_live_max_notional", Number(event.target.value))}
              />
            </Field>
            <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">환경 게이트</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                {state.live_trading_env_enabled ? "활성" : "비활성"}
              </p>
            </div>
            <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">승인 창 상태</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                {approvalStateText(state)}
              </p>
            </div>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <Toggle
              checked={form.manual_live_approval}
              label="수동 승인 정책 사용"
              onChange={(value) => onFieldChange("manual_live_approval", value)}
            />
          </div>
        </div>
      </div>
    </section>
  );
}


