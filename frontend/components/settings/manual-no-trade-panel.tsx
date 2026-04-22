"use client";

import {
  describeManualWindowFlags,
  describeWindowScope,
  formatUtcTimestamp,
  type ManualNoTradeWindowPayload,
} from "../../lib/event-operator-control.js";
import {
  Field,
  InlineFeedback,
  StatusPill,
  Toggle,
  inputClass,
  type FeedbackMessage,
} from "./form-primitives";

type ManualWindowFormState = {
  window_id: string | null;
  scope_type: ManualNoTradeWindowPayload["scope"]["scope_type"];
  symbols: string;
  start_at: string;
  end_at: string;
  reason: string;
  auto_resume: boolean;
  require_manual_rearm: boolean;
  created_by: string;
};

function toTimestamp(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.getTime();
}

export function ManualNoTradePanel({
  manualWindows,
  activeManualWindows,
  manualWindowForm,
  isPending,
  feedback,
  onFieldChange,
  onSave,
  onReset,
  onEdit,
  onEnd,
}: {
  manualWindows: ManualNoTradeWindowPayload[];
  activeManualWindows: ManualNoTradeWindowPayload[];
  manualWindowForm: ManualWindowFormState;
  isPending: boolean;
  feedback?: FeedbackMessage;
  onFieldChange: (
    field: keyof ManualWindowFormState,
    value: ManualWindowFormState[keyof ManualWindowFormState],
  ) => void;
  onSave: () => void;
  onReset: () => void;
  onEdit: (window: ManualNoTradeWindowPayload) => void;
  onEnd: (windowId: string) => void;
}) {
  const now = Date.now();
  const activeWindowsByEnd = [...activeManualWindows].sort(
    (left, right) => (toTimestamp(left.end_at) ?? Number.MAX_SAFE_INTEGER) - (toTimestamp(right.end_at) ?? Number.MAX_SAFE_INTEGER),
  );
  const upcomingWindows = manualWindows
    .filter((window) => {
      const startAt = toTimestamp(window.start_at);
      return typeof startAt === "number" && startAt > now;
    })
    .sort((left, right) => (toTimestamp(left.start_at) ?? Number.MAX_SAFE_INTEGER) - (toTimestamp(right.start_at) ?? Number.MAX_SAFE_INTEGER));
  const summaryWindow = activeWindowsByEnd[0] ?? upcomingWindows[0] ?? manualWindows[0] ?? null;
  const summaryTime = summaryWindow
    ? summaryWindow.is_active
      ? `현재 적용 중 · 종료 ${formatUtcTimestamp(summaryWindow.end_at)}`
      : `다음 시작 ${formatUtcTimestamp(summaryWindow.start_at)}`
    : "정보 없음";
  const summaryScope = summaryWindow ? describeWindowScope(summaryWindow.scope) : "현재 저장된 윈도우 없음";
  const summaryReason = summaryWindow?.reason ?? "현재 저장된 수동 노트레이드 윈도우가 없습니다.";

  return (
    <section className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-slate-900">수동 노트레이드 윈도우</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            기본 화면에서는 현재 수동 차단이 있는지, 몇 개가 켜져 있는지, 가장 가까운 시간과 사유를 먼저 보여줍니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill tone={activeManualWindows.length > 0 ? "danger" : "neutral"}>
            현재 적용 중 {activeManualWindows.length}개
          </StatusPill>
          <StatusPill tone="neutral">저장된 전체 {manualWindows.length}개</StatusPill>
          <StatusPill tone="neutral">UTC 기준</StatusPill>
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-2xl bg-white px-4 py-3">
          <p className="text-xs text-slate-500">현재 활성 수</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{activeManualWindows.length}개</p>
        </div>
        <div className="rounded-2xl bg-white px-4 py-3">
          <p className="text-xs text-slate-500">가장 가까운 시간</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{summaryTime}</p>
        </div>
        <div className="rounded-2xl bg-white px-4 py-3">
          <p className="text-xs text-slate-500">적용 범위</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{summaryScope}</p>
        </div>
        <div className="rounded-2xl bg-white px-4 py-3">
          <p className="text-xs text-slate-500">핵심 사유</p>
          <p className="mt-2 text-sm text-slate-800">{summaryReason}</p>
        </div>
      </div>

      <details className="mt-4 rounded-2xl border border-dashed border-amber-300 bg-white">
        <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-3 px-4 py-4">
          <div>
            <p className="text-sm font-semibold text-slate-900">상세 입력 / 전체 목록</p>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              수동 차단 시간 추가, 수정, 종료와 전체 윈도우 목록 확인은 아래에서 진행합니다.
            </p>
          </div>
          <StatusPill tone="neutral">펼쳐서 수정</StatusPill>
        </summary>

        <div className="border-t border-amber-100 px-4 py-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="적용 범위">
              <select
                className={inputClass}
                value={manualWindowForm.scope_type}
                onChange={(event) =>
                  onFieldChange("scope_type", event.target.value as ManualWindowFormState["scope_type"])
                }
              >
                <option value="global">전체</option>
                <option value="symbols">심볼 지정</option>
              </select>
            </Field>
            <Field label="입력자">
              <input
                className={inputClass}
                value={manualWindowForm.created_by}
                onChange={(event) => onFieldChange("created_by", event.target.value)}
              />
            </Field>
            <Field label="심볼" hint="적용 범위가 심볼 지정일 때만 사용합니다.">
              <input
                className={inputClass}
                value={manualWindowForm.symbols}
                onChange={(event) => onFieldChange("symbols", event.target.value.toUpperCase())}
                placeholder="BTCUSDT, ETHUSDT"
              />
            </Field>
            <Field label="사유">
              <input
                className={inputClass}
                value={manualWindowForm.reason}
                onChange={(event) => onFieldChange("reason", event.target.value)}
                placeholder="거시 이벤트 전후 수동 노트레이드"
              />
            </Field>
            <Field label="적용 시작 시각 (UTC)">
              <input
                className={inputClass}
                type="datetime-local"
                value={manualWindowForm.start_at}
                onChange={(event) => onFieldChange("start_at", event.target.value)}
              />
            </Field>
            <Field label="적용 종료 시각 (UTC)">
              <input
                className={inputClass}
                type="datetime-local"
                value={manualWindowForm.end_at}
                onChange={(event) => onFieldChange("end_at", event.target.value)}
              />
            </Field>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <Toggle
              checked={manualWindowForm.auto_resume}
              label="종료 후 자동 복귀 표시"
              onChange={(value) => onFieldChange("auto_resume", value)}
            />
            <Toggle
              checked={manualWindowForm.require_manual_rearm}
              label="재개 전 수동 확인 표시"
              onChange={(value) => onFieldChange("require_manual_rearm", value)}
            />
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:bg-slate-400"
              disabled={isPending}
              onClick={onSave}
              type="button"
            >
              {manualWindowForm.window_id ? "시간 수정" : "시간 추가"}
            </button>
            <button
              className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700"
              onClick={onReset}
              type="button"
            >
              입력 지우기
            </button>
          </div>

          <div className="mt-3">
            <InlineFeedback message={feedback} />
          </div>

          <div className="mt-4 space-y-3">
            {manualWindows.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-amber-200 px-4 py-4 text-sm text-slate-500">
                현재 저장된 수동 노트레이드 윈도우가 없습니다.
              </div>
            ) : (
              manualWindows.map((window) => (
                <div key={window.window_id} className="rounded-2xl border border-slate-200 bg-canvas p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex flex-wrap gap-2">
                      <StatusPill tone={window.is_active ? "danger" : "neutral"}>
                        {window.is_active ? "활성" : "비활성"}
                      </StatusPill>
                      <StatusPill tone="neutral">{window.window_id}</StatusPill>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        className="rounded-full border border-slate-300 px-3 py-1 text-xs font-semibold text-slate-700"
                        onClick={() => onEdit(window)}
                        type="button"
                      >
                        수정
                      </button>
                      <button
                        className="rounded-full border border-rose-200 px-3 py-1 text-xs font-semibold text-rose-700"
                        onClick={() => onEnd(window.window_id)}
                        type="button"
                      >
                        종료
                      </button>
                    </div>
                  </div>

                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <div className="rounded-2xl bg-white px-4 py-3">
                      <p className="text-xs text-slate-500">적용 범위</p>
                      <p className="mt-2 text-sm font-semibold text-slate-900">
                        {describeWindowScope(window.scope)}
                      </p>
                    </div>
                    <div className="rounded-2xl bg-white px-4 py-3">
                      <p className="text-xs text-slate-500">적용 시간</p>
                      <p className="mt-2 text-sm font-semibold text-slate-900">
                        {formatUtcTimestamp(window.start_at)} ~ {formatUtcTimestamp(window.end_at)}
                      </p>
                    </div>
                    <div className="rounded-2xl bg-white px-4 py-3">
                      <p className="text-xs text-slate-500">사유</p>
                      <p className="mt-2 text-sm text-slate-800">{window.reason}</p>
                    </div>
                    <div className="rounded-2xl bg-white px-4 py-3">
                      <p className="text-xs text-slate-500">부가 플래그</p>
                      <p className="mt-2 text-sm text-slate-800">
                        {describeManualWindowFlags(window.auto_resume, window.require_manual_rearm)}
                      </p>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </details>
    </section>
  );
}
