import Link from "next/link";

import type { OperatorDashboardPayload } from "./overview-dashboard";
import { DataTable } from "./data-table";
import { getSelectedSymbolPolicyHint } from "../lib/selected-symbol";

type Row = Record<string, unknown>;

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

function formatNumber(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined) {
    return "-";
  }
  return value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatRatio(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${(value * 100).toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}%`;
}

function badgeClass(kind: "good" | "warn" | "danger" | "neutral") {
  return {
    good: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warn: "border-amber-200 bg-amber-50 text-amber-800",
    danger: "border-rose-200 bg-rose-50 text-rose-800",
    neutral: "border-slate-200 bg-slate-50 text-slate-700",
  }[kind];
}

function metricCard(title: string, value: string, hint: string) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <p className="text-xs font-medium text-slate-500">{title}</p>
      <p className="mt-2 text-xl font-semibold text-slate-950">{value}</p>
      <p className="mt-2 text-xs leading-5 text-slate-500">{hint}</p>
    </div>
  );
}

function isEntryDecision(value: string | null | undefined) {
  return value === "long" || value === "short";
}

function isSurvivalDecision(value: string | null | undefined) {
  return value === "reduce" || value === "exit";
}

function translateDecision(value: string | null | undefined) {
  if (value === "long") {
    return "롱";
  }
  if (value === "short") {
    return "숏";
  }
  if (value === "reduce") {
    return "축소";
  }
  if (value === "exit") {
    return "청산";
  }
  if (value === "hold") {
    return "보류";
  }
  return value ?? "-";
}

function decisionSummary(decision: string | null | undefined) {
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

function riskSummary(symbol: OperatorDashboardPayload["symbols"][number]) {
  const decision = symbol.risk_guard.decision ?? symbol.ai_decision.decision;
  if (symbol.risk_guard.allowed === null) {
    return {
      label: "risk 평가 없음",
      detail: "-",
    };
  }
  if (symbol.risk_guard.allowed) {
    if (isSurvivalDecision(decision)) {
      return {
        label: "생존 경로 허용",
        detail: translateDecision(decision),
      };
    }
    if (isEntryDecision(decision)) {
      return {
        label: "신규 진입 승인",
        detail: translateDecision(decision),
      };
    }
    return {
      label: "보류 유지",
      detail: translateDecision(decision),
    };
  }
  if (isSurvivalDecision(decision)) {
    return {
      label: "생존 경로 차단",
      detail: translateDecision(decision),
    };
  }
  return {
    label: "신규 진입 차단",
    detail: translateDecision(decision),
  };
}

function SymbolTabs({
  slug,
  symbols,
  selectedSymbol,
  includeAll = false,
}: {
  slug: string;
  symbols: string[];
  selectedSymbol: string;
  includeAll?: boolean;
}) {
  const items = includeAll ? ["ALL", ...symbols] : symbols;
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((symbol) => {
        const active = selectedSymbol === symbol;
        const href =
          symbol === "ALL" ? `/dashboard/${slug}` : `/dashboard/${slug}?symbol=${encodeURIComponent(symbol)}`;
        return (
          <Link
            key={symbol}
            href={href}
            className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${
              active
                ? "border-slate-900 bg-slate-900 text-white"
                : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50"
            }`}
          >
            {symbol === "ALL" ? "전체" : symbol}
          </Link>
        );
      })}
    </div>
  );
}

export function MarketSignalView({
  operator,
  snapshots,
  features,
  selectedSymbol,
}: {
  operator: OperatorDashboardPayload;
  snapshots: Row[];
  features: Row[];
  selectedSymbol: string;
}) {
  const symbols =
    selectedSymbol === "ALL"
      ? operator.symbols
      : operator.symbols.filter((item) => item.symbol === selectedSymbol);

  const filteredSnapshots =
    selectedSymbol === "ALL"
      ? snapshots
      : snapshots.filter((row) => String(row.symbol ?? "").toUpperCase() === selectedSymbol);
  const filteredFeatures =
    selectedSymbol === "ALL"
      ? features
      : features.filter((row) => String(row.symbol ?? "").toUpperCase() === selectedSymbol);

  return (
    <div className="space-y-6">
      <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">시장 / 신호</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">시장 입력과 신호 입력만 확인</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          이 탭은 가격, 스냅샷 시각, 레짐, 신호 입력 근거만 보여줍니다. AI 판단과 risk 차단 정보는
          의사결정 탭으로 이동했습니다.
        </p>
        <div className="mt-4">
          <SymbolTabs
            slug="market"
            symbols={operator.control.tracked_symbols}
            selectedSymbol={selectedSymbol}
            includeAll
          />
        </div>
        <div className="mt-5 grid gap-4 xl:grid-cols-2">
          {symbols.map((symbol) => (
            <div key={symbol.symbol} className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-lg font-semibold text-slate-950">{symbol.symbol}</h3>
                  <p className="mt-1 text-sm text-slate-500">
                    스냅샷 {formatDateTime(symbol.market_snapshot_time)} / {symbol.timeframe ?? "-"}
                  </p>
                </div>
                <span
                  className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                    symbol.stale_flags.length > 0 ? "warn" : "good",
                  )}`}
                >
                  {symbol.stale_flags.length > 0 ? "입력 주의" : "입력 정상"}
                </span>
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                {metricCard("현재가", formatNumber(symbol.latest_price), "선택 심볼 기준 최신 마켓 스냅샷")}
                {metricCard(
                  "레짐",
                  String(symbol.market_context_summary.primary_regime ?? "-"),
                  `정렬 ${String(symbol.market_context_summary.trend_alignment ?? "-")}`,
                )}
                {metricCard(
                  "변동성 / 볼륨",
                  `${String(symbol.market_context_summary.volatility_regime ?? "-")} / ${String(
                    symbol.market_context_summary.volume_regime ?? "-",
                  )}`,
                  "시장 입력 상태만 표시",
                )}
                {metricCard(
                  "입력 freshness",
                  symbol.stale_flags.length > 0 ? "주의" : "정상",
                  symbol.stale_flags.length > 0 ? symbol.stale_flags.join(", ") : "stale flag 없음",
                )}
              </div>
            </div>
          ))}
        </div>
      </section>

      <DataTable
        title="시장 스냅샷"
        description="최근 가격 입력"
        rows={filteredSnapshots}
        emptyStateTitle="표시할 시장 스냅샷이 없습니다."
        emptyStateDescription="선택한 심볼 기준으로 아직 수집된 시장 스냅샷이 없습니다."
        hiddenColumns={["candle_count", "candles", "payload"]}
      />

      <DataTable
        title="신호 입력"
        description="특징량 계산 결과"
        rows={filteredFeatures}
        emptyStateTitle="표시할 신호 입력이 없습니다."
        emptyStateDescription="선택한 심볼 기준으로 아직 계산된 feature snapshot이 없습니다."
      />
    </div>
  );
}

export function DecisionView({
  operator,
  decisionRows,
  selectedSymbol,
}: {
  operator: OperatorDashboardPayload;
  decisionRows: Row[];
  selectedSymbol: string;
}) {
  const symbol =
    operator.symbols.find((item) => item.symbol === selectedSymbol) ?? operator.symbols[0] ?? null;
  const filteredDecisionRows = decisionRows.filter(
    (row) => String(row.symbol ?? "").toUpperCase() === (symbol?.symbol ?? ""),
  );
  const recommendation = symbol ? decisionSummary(symbol.ai_decision.decision) : null;
  const riskOutcome = symbol ? riskSummary(symbol) : null;

  if (symbol === null) {
    return (
      <DataTable
        title="의사결정"
        description="평가 / 판단"
        rows={[]}
        emptyStateTitle="표시할 의사결정이 없습니다."
        emptyStateDescription="tracked symbol이 없거나 아직 평가 데이터가 없습니다."
      />
    );
  }

  return (
    <div className="space-y-6">
      <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">의사결정</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">평가와 판단을 한 화면에서 확인</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          이 탭은 현재 입력을 바탕으로 AI가 무엇을 제안했고, risk_guard가 왜 허용 또는 차단했는지
          보여줍니다. 실제 주문/체결 상태는 overview 또는 orders 화면에서 별도로 확인합니다.
        </p>
        <div className="mt-4">
          <SymbolTabs
            slug="decisions"
            symbols={operator.control.tracked_symbols}
            selectedSymbol={symbol.symbol}
          />
        </div>
        <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {getSelectedSymbolPolicyHint("single")}
        </div>
        <div className="mt-5 grid gap-4 lg:grid-cols-5">
          {metricCard("마지막 평가", formatDateTime(symbol.ai_decision.created_at), "선택 심볼 기준 최신 평가 시각")}
          {metricCard("다음 평가 예정", formatDateTime(operator.control.scheduler_next_run_at), "전역 스케줄 기준")}
          {metricCard("시장 요약", String(symbol.market_context_summary.primary_regime ?? "-"), `정렬 ${String(symbol.market_context_summary.trend_alignment ?? "-")}`)}
          {metricCard("AI 추천", recommendation?.label ?? "-", recommendation?.detail ?? "-")}
          {metricCard("risk 결과", riskOutcome?.label ?? "-", riskOutcome?.detail ?? "-")}
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-[1.1fr,0.9fr]">
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass("neutral")}`}>
                AI 추천
              </span>
              <span className="text-xs text-slate-500">{formatDateTime(symbol.ai_decision.created_at)}</span>
            </div>
            <p className="mt-4 text-2xl font-semibold text-slate-950">{translateDecision(symbol.ai_decision.decision)}</p>
            <p className="mt-2 text-sm text-slate-600">confidence {formatRatio(symbol.ai_decision.confidence)}</p>
            <p className="mt-3 text-sm leading-6 text-slate-700">
              {symbol.ai_decision.explanation_short ?? "최신 판단 설명이 없습니다."}
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {symbol.ai_decision.rationale_codes.length > 0 ? (
                symbol.ai_decision.rationale_codes.map((code) => (
                  <span key={code} className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700">
                    {code}
                  </span>
                ))
              ) : (
                <span className="text-sm text-slate-500">rationale code 없음</span>
              )}
            </div>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                  symbol.risk_guard.allowed === null ? "neutral" : symbol.risk_guard.allowed ? "good" : "danger",
                )}`}
              >
                {riskOutcome?.label ?? "risk 평가 없음"}
              </span>
              <span className="text-xs text-slate-500">{formatDateTime(symbol.risk_guard.created_at)}</span>
            </div>
            <div className="mt-4 space-y-3">
              {metricCard(
                "risk 차단 사유",
                symbol.blocked_reasons.join(", ") || "-",
                "AI 추천 설명이 아니라 최신 risk 판정 기준입니다.",
              )}
              {metricCard("판단 출처", symbol.ai_decision.provider_name ?? "-", `trigger ${symbol.ai_decision.trigger_event ?? "-"}`)}
              {metricCard(
                "승인 프로파일",
                symbol.risk_guard.approved_leverage !== null ? `${symbol.risk_guard.approved_leverage}x` : "-",
                `risk ${symbol.risk_guard.approved_risk_pct ?? 0}`,
              )}
            </div>
          </div>
        </div>
      </section>

      <DataTable
        title="최근 평가 기록"
        description="선택 심볼 의사결정"
        rows={filteredDecisionRows}
        emptyStateTitle="표시할 평가 기록이 없습니다."
        emptyStateDescription="선택한 심볼 기준으로 아직 저장된 decision row가 없습니다."
      />
    </div>
  );
}

export function SchedulerView({
  operator,
  schedulerRows,
}: {
  operator: OperatorDashboardPayload;
  schedulerRows: Row[];
}) {
  return (
    <div className="space-y-6">
      <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">스케쥴러</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">주기와 마지막 실행 상태만 확인</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          이 탭은 평가 판단 자체가 아니라, 언제 실행됐고 언제 다시 실행될지, 최근 성공/실패 상태가
          어떤지에만 집중합니다.
        </p>
        <div className="mt-5 grid gap-4 lg:grid-cols-4">
          {metricCard("현재 상태", operator.control.scheduler_status ?? "-", "최근 스케줄러 실행 상태")}
          {metricCard("실행 윈도우", operator.control.scheduler_window ?? "-", "현재 대표 실행 주기")}
          {metricCard("다음 실행 예정", formatDateTime(operator.control.scheduler_next_run_at), "전역 스케줄 기준")}
          {metricCard(
            "운영 상태",
            operator.control.trading_paused ? "pause" : operator.control.operating_state,
            "판단 상세는 의사결정 탭에서 확인",
          )}
        </div>
      </section>

      <DataTable
        title="스케쥴러 실행 기록"
        description="주기 상태와 결과"
        rows={schedulerRows}
        emptyStateTitle="표시할 스케쥴러 기록이 없습니다."
        emptyStateDescription="아직 scheduler run이 저장되지 않았습니다."
      />
    </div>
  );
}

export function AgentDebugView({ agentRows }: { agentRows: Row[] }) {
  return (
    <div className="space-y-6">
      <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">에이전트</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">디버그 / 고급 정보</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          이 탭은 운영 핵심 판단 화면이 아닙니다. raw provider, role, payload, schema validation 같은
          디버그성 정보만 확인합니다.
        </p>
      </section>

      <DataTable
        title="에이전트 실행 기록"
        description="디버그 / raw metadata"
        rows={agentRows}
        emptyStateTitle="표시할 에이전트 실행 기록이 없습니다."
        emptyStateDescription="저장된 agent run이 아직 없습니다."
      />
    </div>
  );
}
