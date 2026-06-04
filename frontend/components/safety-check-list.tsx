"use client";

import { useState } from "react";

import {
  buildSafetyCheckSummaryView,
  buildSafetyCheckView,
  type SafetyCheckDetailPayload,
  type SafetyCheckSummaryRow,
  type SafetyLabel,
  type SafetyTone,
} from "../lib/safety-checks";

const apiBaseUrl = "";

type DetailState =
  | { status: "loading" }
  | { status: "ready"; data: SafetyCheckDetailPayload }
  | { status: "error"; message: string };

function toneClass(tone: SafetyTone = "neutral") {
  return {
    danger: "bg-rose-50 text-rose-800",
    good: "bg-emerald-50 text-emerald-700",
    neutral: "bg-slate-50 text-slate-700",
    warn: "bg-amber-50 text-amber-800",
  }[tone];
}

function SummaryCell({ label, detail }: { label: string; detail: string }) {
  return (
    <div className="min-w-0 rounded-md bg-slate-50 p-3">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold leading-5 text-slate-950">{detail}</p>
    </div>
  );
}

function DetailRows({ rows }: { rows: SafetyLabel[] }) {
  return (
    <dl className="divide-y divide-slate-100 text-sm">
      {rows.map((row) => (
        <div key={`${row.label}-${row.detail}`} className="grid gap-1 py-3 md:grid-cols-[190px_minmax(0,1fr)]">
          <dt className="font-medium text-slate-500">{row.label}</dt>
          <dd className="min-w-0 break-words font-mono text-xs leading-5 text-slate-800">{row.detail ?? "없음"}</dd>
        </div>
      ))}
    </dl>
  );
}

function RawJsonBlock({ title, value }: { title: string; value: string }) {
  return (
    <section>
      <h3 className="text-sm font-semibold text-slate-950">{title}</h3>
      <pre className="mt-3 max-h-[520px] overflow-auto rounded-md bg-slate-950 p-4 text-xs leading-5 text-slate-50">
        {value}
      </pre>
    </section>
  );
}

function DetailContent({ state }: { state?: DetailState }) {
  if (!state || state.status === "loading") {
    return <p className="text-sm text-slate-600">선택한 리스크 점검 원본을 불러오는 중입니다.</p>;
  }

  if (state.status === "error") {
    return <p className="text-sm text-rose-700">{state.message}</p>;
  }

  const riskCheck = state.data.risk_check;
  if (!riskCheck) {
    return <p className="text-sm text-slate-600">선택한 리스크 점검 원본이 없습니다.</p>;
  }

  const auditEvent = state.data.audit_event ?? null;
  const view = buildSafetyCheckView({
    ...riskCheck,
    audit_event_id: riskCheck.audit_event_id ?? auditEvent?.id,
  });
  const auditRawJson = auditEvent ? JSON.stringify(auditEvent, null, 2) : "연결된 리스크 점검 감사 이벤트 없음";

  return (
    <div className="space-y-5">
      {view.reasonDisplays.length > 0 ? (
        <section>
          <h3 className="text-sm font-semibold text-slate-950">원본 사유 코드</h3>
          <div className="mt-3 flex flex-wrap gap-2">
            {view.reasonDisplays.map((reason) => (
              <span
                key={reason.code}
                className={`rounded-md px-2.5 py-1 text-xs font-semibold ${
                  reason.blocking ? "bg-rose-50 text-rose-800" : "bg-slate-100 text-slate-700"
                }`}
              >
                {reason.label} <code className="font-mono font-medium">({reason.code})</code>
              </span>
            ))}
          </div>
        </section>
      ) : null}

      {view.detailSections.map((section) => (
        <section key={section.title} className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-950">{section.title}</h3>
          <DetailRows rows={section.rows} />
        </section>
      ))}

      <RawJsonBlock title="리스크 점검 원본 JSON" value={view.rawJson} />
      <RawJsonBlock title="감사 이벤트 원본 JSON" value={auditRawJson} />
    </div>
  );
}

export function SafetyCheckList({
  rows,
  errorMessage,
}: {
  rows: SafetyCheckSummaryRow[];
  errorMessage?: string;
}) {
  const [details, setDetails] = useState<Record<string, DetailState>>({});

  async function loadDetail(id: string) {
    const current = details[id];
    if (current?.status === "loading" || current?.status === "ready") {
      return;
    }
    setDetails((previous) => ({ ...previous, [id]: { status: "loading" } }));
    try {
      const response = await fetch(`${apiBaseUrl}/api/risk/checks/${encodeURIComponent(id)}`, { method: "GET" });
      if (!response.ok) {
        throw new Error(`상세 조회 실패: ${response.status}`);
      }
      const data = (await response.json()) as SafetyCheckDetailPayload;
      setDetails((previous) => ({ ...previous, [id]: { status: "ready", data } }));
    } catch (error) {
      setDetails((previous) => ({
        ...previous,
        [id]: { status: "error", message: error instanceof Error ? error.message : "알 수 없는 상세 조회 오류" },
      }));
    }
  }

  if (errorMessage) {
    return (
      <section className="rounded-lg border border-rose-200 bg-rose-50 p-5 text-sm leading-6 text-rose-900">
        <h2 className="text-base font-semibold">안전 점검 기록을 불러오지 못했습니다</h2>
        <p className="mt-2 break-words">{errorMessage}</p>
      </section>
    );
  }

  if (rows.length === 0) {
    return (
      <section className="rounded-lg border border-slate-200 bg-white p-8 text-center text-sm text-slate-600">
        최근 안전 점검 기록이 없습니다
      </section>
    );
  }

  return (
    <section className="space-y-4">
      {rows.map((row) => {
        const view = buildSafetyCheckSummaryView(row);
        return (
          <article key={view.id} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`rounded-md px-2.5 py-1 text-xs font-semibold ${toneClass(view.resultTone)}`}>
                    {view.resultLabel}
                  </span>
                  <span className="rounded-md bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-700">
                    {view.symbol}
                  </span>
                  <span className="text-xs font-medium text-slate-500">{view.createdAtLabel}</span>
                </div>
                <h2 className="mt-3 text-base font-semibold leading-6 text-slate-950">
                  {view.requestedAction} / 리스크 점검 #{view.id}
                </h2>
              </div>
              <span
                className={`w-fit rounded-md px-2.5 py-1 text-xs font-semibold ${
                  view.hasAuditEvent ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"
                }`}
              >
                {view.auditEventLabel}
              </span>
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <SummaryCell label="생성 시각" detail={view.createdAtLabel} />
              <SummaryCell label="심볼" detail={view.symbol} />
              <SummaryCell label="요청 / 의도" detail={`${view.requestedAction} / ${view.intentLabel}`} />
              <SummaryCell label="결과" detail={view.resultLabel} />
              <SummaryCell label="차단 사유" detail={view.blockedReasonSummary} />
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              {view.reasonDisplays.length > 0 ? (
                view.reasonDisplays.map((reason) => (
                  <span
                    key={reason.code}
                    className={`rounded-md px-2.5 py-1 text-xs font-semibold ${
                      reason.blocking ? "bg-rose-50 text-rose-800" : "bg-slate-100 text-slate-700"
                    }`}
                    title={reason.code}
                  >
                    {reason.label}
                  </span>
                ))
              ) : (
                <span className="rounded-md bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-600">
                  차단 사유 코드 없음
                </span>
              )}
            </div>

            <details
              className="mt-4 border-t border-slate-200 pt-4"
              onToggle={(event) => {
                if (event.currentTarget.open) {
                  void loadDetail(view.id);
                }
              }}
            >
              <summary className="cursor-pointer text-sm font-semibold text-slate-800">
                상세 입력/결과와 원본 JSON 보기
              </summary>
              <div className="mt-4">
                <DetailContent state={details[view.id]} />
              </div>
            </details>
          </article>
        );
      })}
    </section>
  );
}
