"use client";

import {
  describeEnforcementMode,
  describeEventBias,
  describeRiskState,
  formatUtcTimestamp,
  type OperatorEventBias,
  type OperatorEventEnforcementMode,
  type OperatorEventRiskState,
  type OperatorEventViewPayload,
} from "../../lib/event-operator-control.js";
import {
  Field,
  InlineFeedback,
  StatusPill,
  inputClass,
  type FeedbackMessage,
} from "./form-primitives";

const operatorBiasOptions: OperatorEventBias[] = ["bullish", "bearish", "neutral", "no_trade", "unknown"];
const operatorRiskStateOptions: OperatorEventRiskState[] = ["risk_on", "risk_off", "neutral", "unknown"];
const operatorEnforcementModeOptions: OperatorEventEnforcementMode[] = [
  "observe_only",
  "approval_required",
  "block_on_conflict",
  "force_no_trade",
] as const;

type OperatorEventFormState = {
  operator_bias: OperatorEventBias;
  operator_risk_state: OperatorEventRiskState;
  applies_to_symbols: string;
  horizon: string;
  valid_from: string;
  valid_to: string;
  enforcement_mode: OperatorEventEnforcementMode;
  note: string;
  created_by: string;
};

function summarizeNote(value: string | null | undefined) {
  const normalized = value?.trim();
  return normalized ? normalized : "저장된 메모 없음";
}

export function OperatorEventPanel({
  operatorEventView,
  operatorEventViewConfigured,
  operatorEventForm,
  isPending,
  feedback,
  onFieldChange,
  onSave,
  onClear,
}: {
  operatorEventView: OperatorEventViewPayload | null;
  operatorEventViewConfigured: boolean;
  operatorEventForm: OperatorEventFormState;
  isPending: boolean;
  feedback?: FeedbackMessage;
  onFieldChange: (
    field: keyof OperatorEventFormState,
    value: OperatorEventFormState[keyof OperatorEventFormState],
  ) => void;
  onSave: () => void;
  onClear: () => void;
}) {
  const appliesToSymbols =
    operatorEventViewConfigured && operatorEventView && operatorEventView.applies_to_symbols.length > 0
      ? operatorEventView.applies_to_symbols.join(", ")
      : "저장된 override 없음";
  const validWindow = operatorEventViewConfigured
    ? `${formatUtcTimestamp(operatorEventView?.valid_from)} ~ ${formatUtcTimestamp(operatorEventView?.valid_to)}`
    : "저장된 시간 범위 없음";
  const enforcementMode = operatorEventViewConfigured
    ? describeEnforcementMode(operatorEventView?.enforcement_mode)
    : "저장된 override 없음 (기본 참고 평가만 사용)";
  const noteSummary = operatorEventViewConfigured
    ? summarizeNote(operatorEventView?.note)
    : "운영자가 저장한 메모 없음";

  return (
    <section className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-slate-900">운영자 이벤트 뷰</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            {operatorEventViewConfigured
              ? "기본 화면에서는 현재 어떤 운영자 정책이 걸려 있는지 먼저 요약해서 보여주고, 상세 입력은 아래에서 펼쳐서 수정합니다."
              : "운영자가 저장한 이벤트 정책은 없습니다. 현재 엔진은 기본 참고 평가만 수행하며, 이 상태 자체로 신규 진입을 차단하지 않습니다."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill tone={operatorEventViewConfigured ? "warn" : "neutral"}>
            {operatorEventViewConfigured ? "운영자 정책 저장됨" : "운영자 정책 미설정"}
          </StatusPill>
          <StatusPill tone="neutral">
            {operatorEventViewConfigured ? describeEnforcementMode(operatorEventView?.enforcement_mode) : "기본 참고 평가"}
          </StatusPill>
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-2xl bg-white px-4 py-3">
          <p className="text-xs text-slate-500">운영자 방향</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {describeEventBias(operatorEventViewConfigured ? operatorEventView?.operator_bias : "unknown")}
          </p>
        </div>
        <div className="rounded-2xl bg-white px-4 py-3">
          <p className="text-xs text-slate-500">운영자 리스크 상태</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {describeRiskState(operatorEventViewConfigured ? operatorEventView?.operator_risk_state : "unknown")}
          </p>
        </div>
        <div className="rounded-2xl bg-white px-4 py-3">
          <p className="text-xs text-slate-500">적용 심볼</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{appliesToSymbols}</p>
        </div>
        <div className="rounded-2xl bg-white px-4 py-3">
          <p className="text-xs text-slate-500">유효 시간</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{validWindow}</p>
        </div>
        <div className="rounded-2xl bg-white px-4 py-3">
          <p className="text-xs text-slate-500">반영 방식</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{enforcementMode}</p>
        </div>
        <div className="rounded-2xl bg-white px-4 py-3">
          <p className="text-xs text-slate-500">메모 요약</p>
          <p className="mt-2 text-sm text-slate-800">{noteSummary}</p>
        </div>
      </div>

      <details className="mt-4 rounded-2xl border border-dashed border-amber-300 bg-white">
        <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-3 px-4 py-4">
          <div>
            <p className="text-sm font-semibold text-slate-900">상세 입력</p>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              운영자 방향, 유효 시간, 반영 방식을 수정하는 입력 폼입니다.
            </p>
          </div>
          <StatusPill tone="neutral">펼쳐서 수정</StatusPill>
        </summary>

        <div className="border-t border-amber-100 px-4 py-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="운영자 방향">
              <select
                className={inputClass}
                value={operatorEventForm.operator_bias}
                onChange={(event) =>
                  onFieldChange(
                    "operator_bias",
                    event.target.value as OperatorEventFormState["operator_bias"],
                  )
                }
              >
                {operatorBiasOptions.map((option) => (
                  <option key={option} value={option}>
                    {describeEventBias(option)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="운영자 리스크 상태">
              <select
                className={inputClass}
                value={operatorEventForm.operator_risk_state}
                onChange={(event) =>
                  onFieldChange(
                    "operator_risk_state",
                    event.target.value as OperatorEventFormState["operator_risk_state"],
                  )
                }
              >
                {operatorRiskStateOptions.map((option) => (
                  <option key={option} value={option}>
                    {describeRiskState(option)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="적용 심볼" hint="비워 두면 모든 심볼에 적용합니다.">
              <input
                className={inputClass}
                value={operatorEventForm.applies_to_symbols}
                onChange={(event) => onFieldChange("applies_to_symbols", event.target.value.toUpperCase())}
                placeholder="BTCUSDT, ETHUSDT"
              />
            </Field>
            <Field label="상황 기간/메모" hint="예: 오늘 밤 이벤트 이후, 이번 주 CPI 발표 이후">
              <input
                className={inputClass}
                value={operatorEventForm.horizon}
                onChange={(event) => onFieldChange("horizon", event.target.value)}
                placeholder="예: 오늘 밤 이벤트 이후"
              />
            </Field>
            <Field label="적용 시작 시각 (UTC)">
              <input
                className={inputClass}
                type="datetime-local"
                value={operatorEventForm.valid_from}
                onChange={(event) => onFieldChange("valid_from", event.target.value)}
              />
            </Field>
            <Field label="적용 종료 시각 (UTC)">
              <input
                className={inputClass}
                type="datetime-local"
                value={operatorEventForm.valid_to}
                onChange={(event) => onFieldChange("valid_to", event.target.value)}
              />
            </Field>
            <Field label="반영 방식" hint="참고만 둘지, 다시 확인할지, 신규 진입을 멈출지 정합니다.">
              <select
                className={inputClass}
                value={operatorEventForm.enforcement_mode}
                onChange={(event) =>
                  onFieldChange(
                    "enforcement_mode",
                    event.target.value as OperatorEventFormState["enforcement_mode"],
                  )
                }
              >
                {operatorEnforcementModeOptions.map((option) => (
                  <option key={option} value={option}>
                    {describeEnforcementMode(option)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="입력자">
              <input
                className={inputClass}
                value={operatorEventForm.created_by}
                onChange={(event) => onFieldChange("created_by", event.target.value)}
              />
            </Field>
          </div>

          <div className="mt-4">
            <Field label="메모" hint="운영자가 나중에 다시 봐도 이해할 수 있을 정도로 간단히 남기면 됩니다.">
              <textarea
                className={inputClass}
                rows={3}
                value={operatorEventForm.note}
                onChange={(event) => onFieldChange("note", event.target.value)}
                placeholder="예: CPI 발표 직후 변동성이 가라앉을 때까지 신규 진입 보수적으로"
              />
            </Field>
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:bg-slate-400"
              disabled={isPending}
              onClick={onSave}
              type="button"
            >
              {isPending ? "저장 중..." : "내용 저장"}
            </button>
            <button
              className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700"
              disabled={isPending}
              onClick={onClear}
              type="button"
            >
              설정 해제
            </button>
          </div>

          <div className="mt-3">
            <InlineFeedback message={feedback} />
          </div>
        </div>
      </details>
    </section>
  );
}
