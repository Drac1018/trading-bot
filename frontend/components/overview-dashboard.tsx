"use client";

import { useEffect, useState } from "react";

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
  event_type: string;
  entity_type: string;
  entity_id: string;
  severity: string;
  message: string;
  created_at: string;
};

export type OperatorDashboardPayload = {
  generated_at: string;
  control: {
    can_enter_new_position: boolean;
    mode: string;
    symbol: string;
    timeframe: string;
    tracked_symbols: string[];
    latest_price: number;
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
    protection_recovery_status: string;
    protected_positions: number;
    unprotected_positions: number;
    open_positions: number;
    scheduler_status: string | null;
    scheduler_window: string | null;
    scheduler_next_run_at: string | null;
  };
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
  ai_decision: {
    decision_run_id: number | null;
    created_at: string | null;
    provider_name: string | null;
    trigger_event: string | null;
    symbol: string | null;
    timeframe: string | null;
    decision: string | null;
    confidence: number | null;
    rationale_codes: string[];
    explanation_short: string | null;
  };
  risk_guard: {
    decision_run_id: number | null;
    created_at: string | null;
    allowed: boolean | null;
    decision: string | null;
    operating_state: string | null;
    reason_codes: string[];
    approved_risk_pct: number | null;
    approved_leverage: number | null;
  };
  execution: {
    order_id: number | null;
    decision_run_id: number | null;
    created_at: string | null;
    symbol: string | null;
    side: string | null;
    order_type: string | null;
    order_status: string | null;
    execution_status: string | null;
    requested_quantity: number | null;
    filled_quantity: number | null;
    average_fill_price: number | null;
    execution_quality: Record<string, unknown>;
  };
  execution_windows: ExecutionWindow[];
  audit_events: AuditEvent[];
};

type Payload = { operator: OperatorDashboardPayload };

function formatNumber(value: number, digits = 0) {
  return value.toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
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

function translateValue(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const map: Record<string, string> = {
    hold: "보류",
    long: "롱",
    short: "숏",
    reduce: "축소",
    exit: "청산",
    TRADABLE: "신규 진입 가능",
    PROTECTION_REQUIRED: "보호 복구 우선",
    DEGRADED_MANAGE_ONLY: "관리 전용",
    EMERGENCY_EXIT: "비상 청산",
    PAUSED: "일시 중지",
    bullish: "상승",
    bearish: "하락",
    range: "횡보",
    transition: "전환",
    bullish_aligned: "상승 정렬",
    bearish_aligned: "하락 정렬",
    active: "개입 중",
    neutral: "중립",
    insufficient_data: "데이터 부족",
    disabled: "비활성화",
    not_paused: "중지 아님",
    completed: "완료",
    running: "실행 중",
    info: "정보",
    warning: "경고",
    error: "오류",
  };
  return map[value] ?? value;
}

function translateReasonCode(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const map: Record<string, string> = {
    TRADING_PAUSED: "운영 중지 상태",
    HOLD_DECISION: "보류 판단",
    LIVE_APPROVAL_REQUIRED: "실거래 승인 필요",
    LIVE_TRADING_DISABLED: "실거래 비활성화",
    PROTECTION_REQUIRED: "보호 주문 복구 필요",
    DEGRADED_MANAGE_ONLY: "관리 전용 상태",
    EMERGENCY_EXIT: "비상 청산 상태",
    MANUAL_USER_REQUEST: "수동 중지",
    PROTECTIVE_ORDER_FAILURE: "보호 주문 이상",
  };
  return map[value] ?? value;
}

function badgeClass(kind: "good" | "warn" | "danger" | "neutral") {
  return {
    good: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warn: "border-amber-200 bg-amber-50 text-amber-800",
    danger: "border-rose-200 bg-rose-50 text-rose-800",
    neutral: "border-slate-200 bg-slate-50 text-slate-700",
  }[kind];
}

function card(title: string, value: string, hint: string) {
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

function Rows({ title, rows, empty }: { title: string; rows: PerformanceEntry[]; empty: string }) {
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
                <span className="text-sm font-semibold text-slate-900">{formatMoney(row.net_realized_pnl_total)}</span>
              </div>
              <p className="mt-2 text-xs text-slate-500">
                승 {row.wins} / 패 {row.losses} / 보류 {row.holds} / 평균 슬리피지 {formatRatio(row.average_slippage_pct)}
              </p>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export function OverviewDashboard({ initial }: { initial: Payload }) {
  const [payload, setPayload] = useState(initial);
  const [lastUpdated, setLastUpdated] = useState(() => new Date());
  const [refreshError, setRefreshError] = useState("");

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const next = await fetchPayload();
        if (!active) return;
        setPayload(next);
        setLastUpdated(new Date());
        setRefreshError("");
      } catch (error) {
        if (!active) return;
        setRefreshError(error instanceof Error ? error.message : "대시보드 갱신 실패");
      }
    };
    const interval = window.setInterval(() => void refresh(), refreshIntervalMs);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const { control, market_signal: market, ai_decision: ai, risk_guard: risk, execution } = payload.operator;
  const blockedReasons = [...control.latest_blocked_reasons, ...control.auto_resume_last_blockers].filter(
    (item, index, array) => item && array.indexOf(item) === index,
  );
  const status =
    control.trading_paused
      ? { kind: "danger" as const, label: "신규 진입 차단", detail: translateReasonCode(control.pause_reason_code) }
      : !control.live_execution_ready
        ? { kind: "warn" as const, label: "가드 모드", detail: control.guard_mode_reason_message ?? "진입 조건 미충족" }
        : { kind: "good" as const, label: "신규 진입 가능", detail: "현재 기준으로 신규 진입과 기존 포지션 관리가 가능합니다." };

  const adaptive = {
    status: translateValue(String(market.adaptive_signal_summary.status ?? "disabled")),
    signalWeight: Number(market.adaptive_signal_summary.signal_weight ?? 1),
    confidenceMultiplier: Number(market.adaptive_signal_summary.confidence_multiplier ?? 1),
    riskMultiplier: Number(market.adaptive_signal_summary.risk_pct_multiplier ?? 1),
    holdBias: Number(market.adaptive_signal_summary.hold_bias ?? 0),
    activeInputs: Array.isArray(market.adaptive_signal_summary.active_inputs)
      ? market.adaptive_signal_summary.active_inputs.map((item) => String(item))
      : [],
  };
  const primaryWindow = market.performance_windows[0];
  const execution24h = payload.operator.execution_windows.find((item) => item.window === "24h");
  const alignedRun = ai.decision_run_id !== null && ai.decision_run_id === execution.decision_run_id;

  return (
    <div className="space-y-6">
      <section className="rounded-[2rem] border border-amber-200/70 bg-white/90 p-6 shadow-frame sm:p-7">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.34em] text-slate-500">운영 메인 화면</p>
            <h1 className="mt-3 font-display text-3xl leading-tight text-slate-950 sm:text-4xl">실거래 운영 흐름</h1>
            <p className="mt-4 max-w-3xl text-sm leading-7 text-slate-600 sm:text-base">
              운영 제어 상태부터 AI 판단, risk_guard, 실행 결과, 감사 이벤트까지 한 흐름으로 정리했습니다.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(status.kind)}`}>{status.label}</span>
            <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass("neutral")}`}>
              {control.symbol} / {control.timeframe}
            </span>
            <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass("neutral")}`}>
              마지막 갱신 {lastUpdated.toLocaleTimeString("ko-KR", { hour12: false })}
            </span>
          </div>
        </div>
        <div className="mt-6 rounded-[1.75rem] bg-slate-950 p-5 text-white">
          <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-white/60">지금 가장 직접적인 상태</p>
          <p className="mt-2 text-base leading-7 sm:text-lg">{status.detail}</p>
          <div className="mt-4 flex flex-wrap gap-3 text-sm text-white/75">
            <span>운영 상태 {translateValue(control.operating_state)}</span>
            <span>자동 복구 {translateValue(control.auto_resume_status)}</span>
            <span>보호 상태 {translateValue(control.protection_recovery_status)}</span>
          </div>
        </div>
        {refreshError ? <p className="mt-4 text-sm text-rose-700">{refreshError}</p> : null}
      </section>

      <section className="rounded-[1.9rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">1. 운영 제어 상태</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">지금 신규 진입 가능한가</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          실거래 승인, pause, degraded, blocked reason을 먼저 확인합니다.
        </p>
        <div className="mt-5 grid gap-4 lg:grid-cols-4">
          {card("신규 진입", control.can_enter_new_position ? "가능" : "차단", control.guard_mode_reason_message ?? "현재 운영 상태 기준")}
          {card(
            "실거래 승인",
            control.approval_armed ? (control.approval_expires_at ? "승인 유효" : "무기한 승인") : "승인 필요",
            control.approval_armed
              ? control.approval_expires_at
                ? `만료 ${formatDateTime(control.approval_expires_at)}`
                : "승인 유지시간 없음"
              : "실거래 승인 창 확인 필요",
          )}
          {card("운영 상태", translateValue(control.operating_state), control.trading_paused ? translateReasonCode(control.pause_reason_code) : "pause 없이 운영 중")}
          {card("보호 포지션", `${control.protected_positions}/${control.open_positions}`, `미보호 포지션 ${control.unprotected_positions}개`)}
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <div className="rounded-2xl border border-slate-200 bg-white">
            {[
              ["pause 사유", translateReasonCode(control.pause_reason_code)],
              ["pause origin", control.pause_origin ?? "-"],
              ["자동 복구 가능", control.auto_resume_eligible ? "가능" : "불가"],
              ["자동 복구 예정", formatDateTime(control.auto_resume_after)],
              ["최근 사이클", control.scheduler_window || control.scheduler_status ? `${control.scheduler_window ?? "-"} / ${translateValue(control.scheduler_status)}` : "-"],
              ["다음 실행 예정", formatDateTime(control.scheduler_next_run_at)],
            ].map(([label, value], index) => (
              <div key={String(label)} className={`flex flex-col gap-1 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between ${index === 0 ? "" : "border-t border-slate-100"}`}>
                <span className="text-slate-500">{label}</span>
                <span className="font-medium text-slate-900">{value}</span>
              </div>
            ))}
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <h3 className="text-sm font-semibold text-slate-950">현재 차단 사유</h3>
            <p className="mt-2 text-sm leading-6 text-slate-600">hold 판단과 별개로, 실제 신규 진입이 막힌 운영/리스크 사유만 보여줍니다.</p>
            <div className="mt-4 space-y-2">
              {blockedReasons.length === 0 ? (
                <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">현재 명시적 차단 사유는 없습니다.</div>
              ) : (
                blockedReasons.map((reason) => (
                  <div key={reason} className="rounded-2xl bg-amber-50 px-4 py-3 text-sm text-slate-800">{translateReasonCode(reason)}</div>
                ))
              )}
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-[1.9rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">2. 시장 / 신호 요약</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">무엇이 수익과 손실을 만들고 있는가</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">최근 24h / 7d / 30d 성과와 신호/레짐/심볼 해석 정보를 함께 봅니다.</p>
        <div className="mt-5 grid gap-4 lg:grid-cols-4">
          {card("현재 가격", formatNumber(control.latest_price, 2), `${control.symbol} / 추적 심볼 ${control.tracked_symbols.join(", ")}`)}
          {card("레짐", translateValue(String(market.market_context_summary.primary_regime ?? "-")), `정렬 ${translateValue(String(market.market_context_summary.trend_alignment ?? "-"))}`)}
          {card(
            "변동성 / 거래량",
            `${String(market.market_context_summary.volatility_regime ?? "-")} / ${String(market.market_context_summary.volume_regime ?? "-")}`,
            `모멘텀 ${String(market.market_context_summary.momentum_state ?? "-")}`,
          )}
          {card("Adaptive bias", adaptive.status, `weight ${formatNumber(adaptive.signalWeight, 2)} / hold bias ${formatNumber(adaptive.holdBias, 2)}`)}
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-3">
          {market.performance_windows.map((window) => {
            const blockedRatio = window.summary.decisions > 0 ? 1 - window.summary.approvals / window.summary.decisions : 0;
            const holdRatio = window.summary.decisions > 0 ? window.summary.holds / window.summary.decisions : 0;
            const winRate = window.summary.wins + window.summary.losses > 0 ? window.summary.wins / (window.summary.wins + window.summary.losses) : 0;
            return (
              <div key={window.window_label} className="rounded-2xl border border-slate-200 bg-white p-4">
                <h3 className="text-sm font-semibold text-slate-950">{window.window_label} 성과 요약</h3>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  {card("순실현 손익", formatMoney(window.summary.net_realized_pnl_total), `수수료 ${formatMoney(window.summary.fee_total)}`)}
                  {card("승률", formatRatio(winRate), `승 ${window.summary.wins} / 패 ${window.summary.losses}`)}
                  {card("보류 비중", formatRatio(holdRatio), `보류 ${window.summary.holds} / 판단 ${window.summary.decisions}`)}
                  {card("차단 비중", formatRatio(blockedRatio), `승인 ${window.summary.approvals} / 판단 ${window.summary.decisions}`)}
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <Rows title="최근 24h 상위 수익 rationale" rows={primaryWindow?.rationale_winners ?? []} empty="최근 24시간 기준 상위 수익 rationale이 없습니다." />
          <Rows title="최근 24h 손실 rationale" rows={primaryWindow?.rationale_losers ?? []} empty="최근 24시간 기준 손실 rationale이 없습니다." />
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <Rows title="레짐별 성과" rows={primaryWindow?.top_regimes ?? []} empty="레짐별 집계가 없습니다." />
          <Rows title="심볼별 성과" rows={primaryWindow?.top_symbols ?? []} empty="심볼별 집계가 없습니다." />
        </div>
      </section>

      <section className="rounded-[1.9rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">3. AI 의사결정</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">AI는 무엇을 제안했는가</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">AI 제안과 이후 risk 승인 결과를 혼동하지 않도록 먼저 분리해 보여줍니다.</p>
        <div className="mt-5 grid gap-4 xl:grid-cols-[1.3fr,0.7fr]">
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass("neutral")}`}>AI 제안</span>
              <span className="text-xs text-slate-500">{formatDateTime(ai.created_at)}</span>
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <p className="text-2xl font-semibold text-slate-950">{translateValue(ai.decision)}</p>
              <p className="text-sm text-slate-600">신뢰도 {formatRatio(Number(ai.confidence ?? 0))}</p>
            </div>
            <p className="mt-3 text-sm leading-6 text-slate-700">{ai.explanation_short ?? "최신 AI 설명이 없습니다."}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              {ai.rationale_codes.length === 0 ? (
                <span className="text-sm text-slate-500">rationale code 없음</span>
              ) : (
                ai.rationale_codes.map((code) => (
                  <span key={code} className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700">{translateReasonCode(code)}</span>
                ))
              )}
            </div>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white">
            {[
              ["provider", ai.provider_name ?? "-"],
              ["trigger event", ai.trigger_event ?? "-"],
              ["symbol / timeframe", `${ai.symbol ?? "-"} / ${ai.timeframe ?? "-"}`],
              ["decision run id", ai.decision_run_id ?? "-"],
            ].map(([label, value], index) => (
              <div key={String(label)} className={`flex flex-col gap-1 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between ${index === 0 ? "" : "border-t border-slate-100"}`}>
                <span className="text-slate-500">{label}</span>
                <span className="font-medium text-slate-900">{value}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="rounded-[1.9rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">4. risk_guard 결과</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">risk_guard는 왜 허용 또는 차단했는가</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">결정론적 가드가 실제로 승인했는지, 차단했다면 왜 막았는지 바로 확인합니다.</p>
        <div className="mt-5 grid gap-4 xl:grid-cols-[1.1fr,0.9fr]">
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(risk.allowed ? "good" : "danger")}`}>{risk.allowed ? "risk 승인" : "risk 차단"}</span>
              <span className="text-xs text-slate-500">{formatDateTime(risk.created_at)}</span>
            </div>
            <p className="mt-4 text-2xl font-semibold text-slate-950">{translateValue(risk.decision)}</p>
            <p className="mt-2 text-sm text-slate-600">운영 상태 {translateValue(risk.operating_state ?? control.operating_state)}</p>
            <div className="mt-4 space-y-2">
              {risk.reason_codes.length === 0 ? (
                <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">명시적 차단 사유가 없습니다.</div>
              ) : (
                risk.reason_codes.map((code) => (
                  <div key={code} className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-800">{translateReasonCode(code)}</div>
                ))
              )}
            </div>
          </div>
          <div className="grid gap-4">
            {card("승인 risk_pct", risk.approved_risk_pct !== null ? formatRatio(risk.approved_risk_pct) : "-", "허용된 최대 손실 비중")}
            {card("승인 leverage", risk.approved_leverage !== null ? `${formatNumber(risk.approved_leverage, 2)}x` : "-", "risk_guard가 최종 승인한 레버리지")}
          </div>
        </div>
      </section>

      <section className="rounded-[1.9rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">5. 실제 실행 결과</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">실제로 주문과 체결이 되었는가</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">신호 품질과 execution 품질을 구분할 수 있도록 주문 상태와 체결 품질을 따로 보여줍니다.</p>
        <div className="mt-5 grid gap-4 xl:grid-cols-[1.2fr,0.8fr]">
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(execution.order_status === "filled" ? "good" : execution.order_id ? "warn" : "neutral")}`}>{execution.order_id ? "실행 결과" : "주문 없음"}</span>
              <span className="text-xs text-slate-500">{formatDateTime(execution.created_at)}</span>
            </div>
            {execution.order_id ? (
              <>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  {card("주문 상태", `${execution.order_type ?? "-"} / ${execution.order_status ?? "-"}`, `${execution.symbol ?? "-"} / ${execution.side ?? "-"}`)}
                  {card("체결 상태", execution.execution_status ?? "체결 없음", `평균 체결가 ${execution.average_fill_price !== null ? formatNumber(execution.average_fill_price, 2) : "-"}`)}
                  {card("요청 / 체결 수량", `${execution.requested_quantity ?? 0} / ${execution.filled_quantity ?? 0}`, "부분 체결 여부 확인")}
                  {card("실행 품질", String(execution.execution_quality.execution_quality_status ?? "-"), String(execution.execution_quality.decision_quality_status ?? "decision 품질 정보 없음"))}
                </div>
                {!alignedRun && ai.decision_run_id !== null ? <p className="mt-4 text-sm text-amber-700">현재 실행 결과는 최신 AI 판단과 직접 연결되지 않은 가장 최근 주문일 수 있습니다.</p> : null}
              </>
            ) : (
              <div className="mt-4 rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">최신 판단에 연결된 실주문 또는 체결 결과가 없습니다. hold 또는 risk 차단일 수 있습니다.</div>
            )}
          </div>
          <div className="grid gap-4">
            {card("최근 24h 평균 슬리피지", `${formatNumber(Number(execution24h?.execution_quality_summary.average_realized_slippage_pct ?? 0), 2)}%`, "실제 체결 기준 execution 품질")}
            {card("최근 24h 부분 체결", String(execution24h?.execution_quality_summary.partial_fill_orders ?? 0), "partial fill 반복 여부")}
            {card("최근 24h 재호가", String(execution24h?.execution_quality_summary.repriced_orders ?? 0), "reprice 정책 개입 횟수")}
            {card("최근 24h 공격적 fallback", String(execution24h?.execution_quality_summary.aggressive_fallback_orders ?? 0), "시장가 completion 빈도")}
          </div>
        </div>
      </section>

      <section className="rounded-[1.9rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">보조 해석</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">hold 증가 원인과 adaptive 개입</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">hold가 늘어난 조건과 adaptive bias는 차단 사유와 다른 개념이므로 따로 봅니다.</p>
        <div className="mt-5 grid gap-4 xl:grid-cols-2">
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <h3 className="text-sm font-semibold text-slate-950">hold 증가 원인</h3>
            <div className="mt-4 space-y-2">
              {market.hold_blocked_summary.hold_top_conditions.length === 0 ? (
                <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">최근 hold 집중 조건이 없습니다.</div>
              ) : (
                market.hold_blocked_summary.hold_top_conditions.map((item) => (
                  <div key={item.key} className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-700">{item.key}</div>
                ))
              )}
            </div>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <h3 className="text-sm font-semibold text-slate-950">현재 adaptive 개입 수준</h3>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {card("상태", adaptive.status, `입력 ${adaptive.activeInputs.length > 0 ? adaptive.activeInputs.join(", ") : "중립"}`)}
              {card("signal weight", formatNumber(adaptive.signalWeight, 2), "최근 성과 기반 가중치")}
              {card("confidence 배수", `${formatNumber(adaptive.confidenceMultiplier, 2)}x`, "손실 구간에서는 1보다 낮아집니다.")}
              {card("risk 배수", `${formatNumber(adaptive.riskMultiplier, 2)}x`, `hold bias ${formatNumber(adaptive.holdBias, 2)}`)}
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-[1.9rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">6. 최근 감사 이벤트</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">무슨 운영 이벤트가 발생했는가</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">pause, 차단, 동기화, 실행 이상 여부를 시간순으로 추적할 때 사용합니다.</p>
        <div className="mt-5 space-y-3">
          {payload.operator.audit_events.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-slate-300 px-4 py-5 text-sm text-slate-500">최근 감사 이벤트가 없습니다.</div>
          ) : (
            payload.operator.audit_events.map((event) => (
              <div key={`${event.event_type}-${event.entity_id}-${event.created_at}`} className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(event.severity === "error" ? "danger" : event.severity === "warning" ? "warn" : "neutral")}`}>{translateValue(event.severity)}</span>
                  <span className="text-xs text-slate-500">{formatDateTime(event.created_at)}</span>
                  <span className="text-xs text-slate-500">{event.event_type} / {event.entity_type}:{event.entity_id}</span>
                </div>
                <p className="mt-3 text-sm leading-6 text-slate-800">{event.message}</p>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
