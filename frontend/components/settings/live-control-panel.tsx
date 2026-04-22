"use client";

import { Field, InlineFeedback, StatusPill, Toggle, inputClass, type FeedbackMessage } from "./form-primitives";
import { formatDisplayValue } from "../../lib/ui-copy";
import { type ControlStatusSummary, type LiveSyncResult, type RolloutMode } from "./types";

type LiveControlState = {
  trading_paused: boolean;
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
  auto_resume_last_blockers: string[];
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
  return values.map((item) => formatDisplayValue(item)).join(", ");
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

function ControlStatusPanel({
  state,
  summary,
}: {
  state: LiveControlState;
  summary: ControlStatusSummary;
}) {
  const currentCycleBlockedReasons = summary.blocked_reasons_current_cycle;
  const approvalBlockedReasons = summary.approval_control_blocked_reasons ?? [];
  const primaryBlocker = currentCycleBlockedReasons[0];
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
      label: "거래소 canTrade",
      value:
        summary.exchange_can_trade === null
          ? "미확인"
          : summary.exchange_can_trade
            ? "주문 가능"
            : "주문 차단",
      detail:
        summary.exchange_can_trade === null
          ? "최근 계좌 동기화에 거래소 canTrade 상태가 없습니다."
          : summary.exchange_can_trade
            ? "거래소 계좌 상태 기준으로 신규 주문이 가능합니다."
            : "거래소 계좌 상태 기준으로 신규 주문이 차단됩니다.",
      tone:
        summary.exchange_can_trade === null
          ? ("neutral" as const)
          : summary.exchange_can_trade
            ? ("good" as const)
            : ("danger" as const),
    },
    {
      label: "앱 live arm",
      value: summary.app_live_armed ? "Arm됨" : "Arm 해제",
      detail: summary.app_live_armed
        ? "앱 실거래 경로가 arm 상태입니다."
        : "앱 live arm이 내려가 있어 실거래 경로가 열리지 않습니다.",
      tone: summary.app_live_armed ? ("good" as const) : ("warn" as const),
    },
    {
      label: "실거래 승인 창",
      value: summary.approval_window_open ? "열림" : "닫힘",
      detail: summary.approval_window_open
        ? state.live_execution_armed_until
          ? `만료 ${formatDisplayValue(state.live_execution_armed_until, "live_execution_armed_until")}`
          : "실거래 승인 창이 현재 유효합니다."
        : "신규 진입 전 실거래 승인 창을 다시 열어야 합니다.",
      tone: summary.approval_window_open ? ("good" as const) : ("warn" as const),
    },
    {
      label: "pause",
      value: summary.paused ? "중지" : "운영 중",
      detail: summary.paused
        ? formatDisplayValue(state.pause_reason_code, "pause_reason_code")
        : "운영 중지 플래그가 활성화되어 있지 않습니다.",
      tone: summary.paused ? ("danger" as const) : ("good" as const),
    },
    {
      label: "degraded",
      value: summary.degraded ? "관리 전용" : "정상",
      detail: summary.degraded
        ? `${formatDisplayValue(state.operating_state, "operating_state")} / 보호 복구 ${formatDisplayValue(state.protection_recovery_status, "protection_recovery_status")}`
        : "관리 전용 또는 비상 복구 상태로 내려가 있지 않습니다.",
      tone: summary.degraded ? ("warn" as const) : ("good" as const),
    },
    {
      label: "risk 허용",
      value:
        summary.risk_allowed === null
          ? "미평가"
          : summary.risk_allowed
            ? "허용"
            : "차단",
      detail:
        summary.risk_allowed === null
          ? "현재 cycle risk 결과가 아직 집계되지 않았습니다."
          : summary.risk_allowed
            ? "현재 cycle risk_guard가 신규 진입을 허용했습니다."
            : primaryBlocker
              ? formatDisplayValue(primaryBlocker, "blocked_reason_codes")
              : state.guard_mode_reason_message ?? "현재 cycle risk_guard가 신규 진입을 차단했습니다.",
      tone:
        summary.risk_allowed === null
          ? ("neutral" as const)
          : summary.risk_allowed
            ? ("good" as const)
            : ("danger" as const),
    },
  ];

  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-3 md:grid-cols-2">
        {cards.map((card) => (
          <div key={card.label} className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-medium text-slate-500">{card.label}</p>
              <StatusPill tone={card.tone}>{card.value}</StatusPill>
            </div>
            <p className="mt-3 text-sm leading-6 text-slate-700">{card.detail}</p>
          </div>
        ))}
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-slate-900">현재 cycle 차단 사유</p>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              과거 blocker나 auto-resume blocker를 섞지 않고, 지금 cycle 기준으로 신규 진입을 막는 이유만 보여줍니다.
            </p>
          </div>
          <StatusPill tone={currentCycleBlockedReasons.length > 0 ? "warn" : "good"}>
            {currentCycleBlockedReasons.length > 0 ? `${currentCycleBlockedReasons.length}건` : "없음"}
          </StatusPill>
        </div>
        <div className="mt-4 space-y-2">
          {currentCycleBlockedReasons.length === 0 ? (
            <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
              현재 cycle 기준 차단 사유는 없습니다.
            </div>
          ) : (
            currentCycleBlockedReasons.map((reason) => (
              <div key={reason} className="rounded-2xl bg-amber-50 px-4 py-3 text-sm text-slate-800">
                {formatDisplayValue(reason, "blocked_reason_codes")}
              </div>
            ))
          )}
        </div>
        {state.auto_resume_last_blockers.length > 0 ? (
          <div className="mt-4 rounded-2xl border border-dashed border-slate-200 px-4 py-3 text-sm text-slate-600">
            자동 복구 차단 사유: {formatCodeList(state.auto_resume_last_blockers)}
          </div>
        ) : null}
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-slate-900">approval control summary</p>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              can_enter_new_position 외에도 승인/운영 제어 관점에서 현재 차단 사유를 분리해 보여줍니다.
            </p>
          </div>
          <StatusPill tone={approvalBlockedReasons.length > 0 ? "danger" : "good"}>
            {approvalBlockedReasons.length > 0 ? `${approvalBlockedReasons.length}건` : "정상"}
          </StatusPill>
        </div>
        <div className="mt-4 space-y-2">
          {approvalBlockedReasons.length === 0 ? (
            <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
              승인/운영 제어 관점에서 즉시 차단 사유가 없습니다.
            </div>
          ) : (
            approvalBlockedReasons.map((reason) => (
              <div key={reason} className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-900">
                {formatDisplayValue(reason, "blocked_reason_codes")}
              </div>
            ))
          )}
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
    <div className="mt-3 space-y-3 rounded-2xl border border-amber-200 bg-white p-4">
      <div className="flex flex-wrap gap-2">
        <StatusPill>동기화 심볼 {result.symbols?.join(", ") ?? "-"}</StatusPill>
        <StatusPill>주문 {result.synced_orders ?? 0}</StatusPill>
        <StatusPill>포지션 {result.synced_positions ?? 0}</StatusPill>
        {typeof result.equity === "number" ? (
          <StatusPill>자산 {formatDisplayValue(result.equity, "equity")}</StatusPill>
        ) : null}
      </div>
      <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-600">
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
        <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="text-xs text-slate-500">누락 보호 항목</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{missingProtectionText}</p>
        </div>
      </div>
      {protectionEntries.length > 0 ? (
        <div className="grid gap-3 md:grid-cols-2">
          {protectionEntries.map(([symbol, state]) => (
            <div key={symbol} className="rounded-2xl bg-canvas px-4 py-3">
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
        <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
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
  onPause,
  onResume,
  onArm,
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
  onPause: () => void;
  onResume: () => void;
  onArm: () => void;
  onDisarm: () => void;
  onSync: () => void;
  onFieldChange: (field: keyof LiveControlForm, value: LiveControlForm[keyof LiveControlForm]) => void;
}) {
  return (
    <section className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div>
          <h3 className="text-lg font-semibold text-slate-900">실거래 제어</h3>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
            운영 중지, 승인 창 제어, 거래소 재동기화처럼 즉시 반응이 필요한 제어를 상단 핵심 패널로 모았습니다.
            아래 상태는 백엔드가 내려준 현재 gate 요약이며, 심볼별 세부 흐름은 개요 화면에서 확인합니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill tone={state.trading_paused ? "danger" : "good"}>
            {state.trading_paused ? "pause 활성" : "운영 중"}
          </StatusPill>
          <StatusPill tone={state.live_execution_armed ? "good" : "warn"}>
            {state.live_execution_armed ? "approval armed" : "approval 닫힘"}
          </StatusPill>
          <StatusPill tone={state.live_execution_ready ? "good" : "warn"}>
            {state.live_execution_ready ? "live ready" : "live guard"}
          </StatusPill>
          <StatusPill tone={state.exchange_submit_allowed ? "good" : "neutral"}>
            {state.exchange_submit_allowed ? "실주문 제출 허용" : "실주문 제출 제한"}
          </StatusPill>
        </div>
      </div>

      <ControlStatusPanel state={state} summary={summary} />

      <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-slate-900">즉시 실행 액션</p>
              <p className="mt-1 text-sm leading-6 text-slate-600">
                pause/resume/live arm/sync는 저장된 현재 설정 기준으로만 실행합니다.
              </p>
            </div>
            <StatusPill tone="neutral">저장값 기준 실행</StatusPill>
          </div>
          <p className="mt-4 text-sm leading-6 text-slate-600">
            즉시 중지는 신규 진입만 막는 운영 중지입니다. 기존 포지션의 보호 주문 유지, 축소, 비상 청산은 계속 허용됩니다.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button className="rounded-full bg-rose-600 px-4 py-2 text-sm font-semibold text-white" onClick={onPause} type="button">
              즉시 중지
            </button>
            <button className="rounded-full bg-emerald-600 px-4 py-2 text-sm font-semibold text-white" onClick={onResume} type="button">
              중지 해제
            </button>
            <button
              className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-400"
              disabled={liveArmBlocked}
              onClick={onArm}
              type="button"
            >
              실거래 승인
            </button>
            <button className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700" onClick={onDisarm} type="button">
              승인 해제
            </button>
            <button className="rounded-full border border-amber-200 px-4 py-2 text-sm font-semibold text-slate-700" onClick={onSync} type="button">
              거래소 동기화
            </button>
          </div>
          <p className="mt-3 text-xs leading-5 text-slate-500">
            실거래 승인 시간: 저장값 {state.live_approval_window_minutes}분 / 동기화 심볼: 저장값 {state.default_symbol}
            {actionsUseSavedSettings ? " / 현재 form 입력과 저장값이 다르면 저장 후 다시 실행하세요." : ""}
          </p>
          <div className="mt-3">
            <InlineFeedback message={feedback} />
          </div>
          {liveArmBlocked && liveArmDisableReason ? (
            <p className="mt-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
              실거래 승인 버튼 비활성화 사유: {liveArmDisableReason}
            </p>
          ) : null}
          <LiveSyncPanel result={liveSyncResult} />
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-slate-900">저장형 운영 기본값</p>
              <p className="mt-1 text-sm leading-6 text-slate-600">
                운영 모드, 승인 유지 시간, limited live 한도는 저장 후에만 런타임에 반영됩니다.
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
            </Field>
            <Field label="limited live 주문당 최대 notional">
              <input
                className={inputClass}
                min={1}
                step="1"
                type="number"
                value={form.limited_live_max_notional ?? 500}
                onChange={(event) => onFieldChange("limited_live_max_notional", Number(event.target.value))}
              />
            </Field>
            <div className="rounded-2xl border border-amber-200 bg-canvas px-4 py-3">
              <p className="text-xs text-slate-500">환경 게이트</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                {state.live_trading_env_enabled ? "활성" : "비활성"}
              </p>
            </div>
            <div className="rounded-2xl border border-amber-200 bg-canvas px-4 py-3">
              <p className="text-xs text-slate-500">승인 창 상태</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                {state.live_execution_armed
                  ? `열림 (${formatDisplayValue(state.live_execution_armed_until, "live_execution_armed_until")})`
                  : "닫힘"}
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
