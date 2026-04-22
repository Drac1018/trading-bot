"use client";

import {
  describeAlignmentStatus,
  describeEffectivePolicyPreview,
  describeEventBias,
  describeEventReasonCode,
  describeImportance,
  describePolicySource,
  describeRiskState,
  describeSourceStatus,
  formatUtcTimestamp,
  toneForAlignment,
  toneForPolicyPreview,
  toneForSourceStatus,
  type AIEventViewPayload,
  type AlignmentDecisionPayload,
  type OperatorEffectivePolicyPreview,
  type OperatorEventContextPayload,
  type OperatorPolicySource,
} from "../../lib/event-operator-control.js";
import { StatusPill } from "./form-primitives";

function toneForAlignmentReason(reason: string) {
  if (reason.includes("conflict") || reason.includes("no_trade")) {
    return "danger" as const;
  }
  if (
    reason.includes("unavailable") ||
    reason.includes("outside_valid_window") ||
    reason.includes("insufficient_data")
  ) {
    return "warn" as const;
  }
  return "neutral" as const;
}

export function EventResponseOverviewPanel({
  defaultSymbol,
  eventContext,
  aiEventView,
  operatorEventViewConfigured,
  blockedReason,
  approvalRequiredReason,
  alignmentDecision,
  eventSourceProvenanceLabel,
  effectivePolicyPreview,
  policySource,
  entryPolicySummary,
  alignmentReasonSummary,
  eventSourceHelp,
}: {
  defaultSymbol: string;
  eventContext: OperatorEventContextPayload | null;
  aiEventView: AIEventViewPayload | null;
  operatorEventViewConfigured: boolean;
  blockedReason: string | null | undefined;
  approvalRequiredReason: string | null | undefined;
  alignmentDecision: AlignmentDecisionPayload | null;
  eventSourceProvenanceLabel: string;
  effectivePolicyPreview: OperatorEffectivePolicyPreview | null | undefined;
  policySource: OperatorPolicySource | null | undefined;
  entryPolicySummary: string;
  alignmentReasonSummary: string;
  eventSourceHelp: string;
}) {
  const usesDefaultReferenceOnly =
    !operatorEventViewConfigured &&
    !blockedReason &&
    !approvalRequiredReason &&
    (policySource ?? "none") === "none";

  return (
    <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-slate-900">이벤트 대응 현황</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            시장 분위기와 별도로 이벤트 전후에 신규 진입을 어떻게 다룰지 보여주는 화면입니다. 실제 신규
            진입 판단과도 같은 기준을 사용합니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill tone="neutral">{defaultSymbol}</StatusPill>
          <StatusPill tone={toneForSourceStatus(eventContext?.source_status)}>
            {describeSourceStatus(eventContext?.source_status, { kind: "event_context" })}
          </StatusPill>
          <StatusPill tone="neutral">{eventSourceProvenanceLabel}</StatusPill>
          <StatusPill tone={usesDefaultReferenceOnly ? "neutral" : toneForPolicyPreview(effectivePolicyPreview)}>
            {usesDefaultReferenceOnly ? "기본 참고 평가" : "실제 진입 판단과 동일"}
          </StatusPill>
        </div>
      </div>

      <div className="mt-4 rounded-2xl border border-slate-200 bg-white px-4 py-4">
        <p className="text-xs text-slate-500">신규 진입 1줄 요약</p>
        <p className="mt-2 text-sm font-semibold text-slate-900">{entryPolicySummary}</p>
        <p className="mt-2 text-xs text-slate-500">데이터 안내: {eventSourceHelp}</p>
      </div>

      <div className="mt-4 space-y-4">
        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-slate-900">예정 이벤트 리스크</p>
              <p className="mt-1 text-sm text-slate-600">데이터가 늦거나 일부 비어 있어도 그대로 드러내 줍니다.</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <StatusPill tone={toneForSourceStatus(eventContext?.source_status)}>
                {describeSourceStatus(eventContext?.source_status, { kind: "event_context" })}
              </StatusPill>
              <StatusPill tone="neutral">{eventSourceProvenanceLabel}</StatusPill>
              {eventContext?.is_stale ? <StatusPill tone="warn">지연</StatusPill> : null}
              {eventContext && !eventContext.is_complete ? <StatusPill tone="warn">불완전</StatusPill> : null}
            </div>
          </div>
          <p className="mt-3 text-xs text-slate-500">{eventSourceHelp}</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">다음 이벤트</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{eventContext?.next_event_name ?? "정보 없음"}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">이벤트 시각</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{formatUtcTimestamp(eventContext?.next_event_at)}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">남은 시간</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                {typeof eventContext?.minutes_to_next_event === "number" ? `${eventContext.minutes_to_next_event}분` : "정보 없음"}
              </p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">중요도</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{describeImportance(eventContext?.next_event_importance)}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">위험 구간</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                {eventContext?.active_risk_window ? "활성" : "비활성"}
              </p>
              <p className="mt-2 text-xs text-slate-500">
                {eventContext?.active_risk_window_detail?.event_name ?? eventContext?.summary_note ?? "추가 안내 없음"}
              </p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">생성 시각</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{formatUtcTimestamp(eventContext?.generated_at)}</p>
            </div>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <p className="text-sm font-semibold text-slate-900">AI 이벤트 뷰</p>
          <p className="mt-1 text-sm text-slate-600">AI가 이벤트 관련 의견을 남기지 않았으면 그대로 "미설정"으로 표시합니다.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <StatusPill tone={toneForSourceStatus(aiEventView?.source_state)}>
              {describeSourceStatus(aiEventView?.source_state, { kind: "ai_event_view" })}
            </StatusPill>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">AI 방향</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{describeEventBias(aiEventView?.ai_bias)}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">AI 리스크 상태</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{describeRiskState(aiEventView?.ai_risk_state)}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">AI 신뢰도</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                {typeof aiEventView?.ai_confidence === "number" ? aiEventView.ai_confidence.toFixed(2) : "정보 없음"}
              </p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">AI 상태</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                {describeSourceStatus(aiEventView?.source_state, { kind: "ai_event_view" })}
              </p>
            </div>
          </div>
          <div className="mt-3 grid gap-3">
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">시나리오 메모</p>
              <p className="mt-2 text-sm text-slate-800">{aiEventView?.scenario_note ?? "정보 없음"}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">신뢰도 조정 이유</p>
              <p className="mt-2 text-sm text-slate-800">{aiEventView?.confidence_penalty_reason ?? "정보 없음"}</p>
            </div>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-slate-900">정렬 결과</p>
              <p className="mt-1 text-sm text-slate-600">AI 의견과 운영자 설정을 비교한 결과입니다.</p>
            </div>
            <StatusPill tone={toneForAlignment(alignmentDecision?.alignment_status)}>
              {describeAlignmentStatus(alignmentDecision?.alignment_status)}
            </StatusPill>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {alignmentDecision && alignmentDecision.reason_codes.length > 0 ? (
              alignmentDecision.reason_codes.map((reason) => (
                <StatusPill key={reason} tone={toneForAlignmentReason(reason)}>
                  {describeEventReasonCode(reason)}
                </StatusPill>
              ))
            ) : (
              <StatusPill tone="neutral">추가 사유 없음</StatusPill>
            )}
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">판단 사유</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{alignmentReasonSummary}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">평가 시각</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{formatUtcTimestamp(alignmentDecision?.evaluated_at)}</p>
            </div>
          </div>
          <div className="mt-3 rounded-2xl border border-dashed border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            {usesDefaultReferenceOnly
              ? "운영자가 저장한 이벤트 정책은 없습니다. 현재 엔진은 기본 참고 평가만 수행하며, 이 상태 자체로 신규 진입을 차단하지 않습니다."
              : `지금 신규 진입은 "${describeEffectivePolicyPreview(effectivePolicyPreview)}" 상태입니다. 이번 판단은 ${describePolicySource(policySource)} 기준으로 계산되며, 청산·축소 같은 안전 조치는 계속 허용합니다.`}
          </div>
        </div>
      </div>
    </div>
  );
}
