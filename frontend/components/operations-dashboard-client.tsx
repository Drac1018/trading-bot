"use client";

import { useEffect, useMemo, useState } from "react";

import type { AIUsagePayload } from "./ai-usage-panel";
import { DecisionView, RiskView, SchedulerView } from "./dashboard-views";
import type { OperatorDashboardPayload } from "./overview-dashboard";
import { fetchJson } from "../lib/api";
import { resolveSelectedSymbol } from "../lib/selected-symbol";

type SearchParams = Record<string, string | string[] | undefined>;
type Row = Record<string, unknown>;
type OperationsSection = "decisions" | "risk" | "scheduler";
type DecisionEntryFlowTab = "summary" | "plan" | "execution";

type OperationsState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | {
      status: "ready";
      operator: OperatorDashboardPayload;
      decisionRows?: Row[];
      riskRows?: Row[];
      alertRows?: Row[];
      schedulerRows?: Row[];
      aiUsage?: AIUsagePayload;
    };

function queryValue(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

function resolveDecisionEntryFlowTab(value: string | string[] | undefined): DecisionEntryFlowTab {
  const normalized = queryValue(value);
  if (normalized === "plan" || normalized === "execution") {
    return normalized;
  }
  return "summary";
}

function errorMessage(error: unknown) {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "운영 데이터를 불러오지 못했습니다.";
}

export function OperationsDashboardClient({
  section,
  query,
}: {
  section: OperationsSection;
  query: SearchParams;
}) {
  const [state, setState] = useState<OperationsState>({ status: "loading" });
  const [retry, setRetry] = useState(0);
  const requestKey = useMemo(
    () =>
      JSON.stringify({
        section,
        symbol: queryValue(query.symbol),
        flow: queryValue(query.flow),
      }),
    [section, query.flow, query.symbol],
  );

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });

    async function load() {
      try {
        if (section === "risk") {
          const [operator, riskRows, alertRows, aiUsage] = await Promise.all([
            fetchJson<OperatorDashboardPayload>("/api/dashboard/operator?view=risk"),
            fetchJson<Row[]>("/api/risk/checks?limit=12&compact=true"),
            fetchJson<Row[]>("/api/alerts?limit=20&acknowledged=false"),
            fetchJson<AIUsagePayload>("/api/settings/ai-usage"),
          ]);
          if (!cancelled) {
            setState({ status: "ready", operator, riskRows, alertRows, aiUsage });
          }
          return;
        }

        if (section === "decisions") {
          const [operator, decisionRows] = await Promise.all([
            fetchJson<OperatorDashboardPayload>("/api/dashboard/operator?view=decision"),
            fetchJson<Row[]>("/api/decisions?limit=12&compact=true"),
          ]);
          if (!cancelled) {
            setState({ status: "ready", operator, decisionRows });
          }
          return;
        }

        const [operator, schedulerRows] = await Promise.all([
          fetchJson<OperatorDashboardPayload>("/api/dashboard/operator?view=scheduler"),
          fetchJson<Row[]>("/api/scheduler?limit=20&compact=true"),
        ]);
        if (!cancelled) {
          setState({ status: "ready", operator, schedulerRows });
        }
      } catch (error) {
        if (!cancelled) {
          setState({ status: "error", message: errorMessage(error) });
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [requestKey, retry, section]);

  if (state.status === "loading") {
    return (
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6" role="status" aria-live="polite">
        <p className="text-sm font-semibold text-slate-950">운영 데이터를 불러오는 중입니다.</p>
        <p className="mt-2 text-sm leading-6 text-slate-600">백엔드 상태, risk 승인/차단, AI 생략 사유를 확인하고 있습니다.</p>
      </section>
    );
  }

  if (state.status === "error") {
    return (
      <section className="rounded-lg border border-rose-200 bg-rose-50 p-5 shadow-sm sm:p-6" role="alert">
        <p className="text-sm font-semibold text-rose-950">운영 데이터를 불러오지 못했습니다.</p>
        <p className="mt-2 break-words text-sm leading-6 text-rose-700">{state.message}</p>
        <button
          type="button"
          onClick={() => setRetry((value) => value + 1)}
          className="mt-4 min-h-10 rounded-md border border-rose-300 bg-white px-4 text-sm font-semibold text-rose-700 hover:bg-rose-100"
        >
          다시 시도
        </button>
      </section>
    );
  }

  if (section === "risk") {
    return (
      <RiskView
        operator={state.operator}
        riskRows={state.riskRows ?? []}
        alertRows={state.alertRows ?? []}
        aiUsage={state.aiUsage ?? ({} as AIUsagePayload)}
        summaryMode
      />
    );
  }

  if (section === "decisions") {
    const selectedSymbol = resolveSelectedSymbol(
      queryValue(query.symbol),
      state.operator.control.tracked_symbols,
      state.operator.control.default_symbol,
      { mode: "single" },
    );
    return (
      <DecisionView
        operator={state.operator}
        decisionRows={state.decisionRows ?? []}
        selectedSymbol={selectedSymbol}
        entryFlowTab={resolveDecisionEntryFlowTab(query.flow)}
      />
    );
  }

  return <SchedulerView operator={state.operator} schedulerRows={state.schedulerRows ?? []} />;
}
