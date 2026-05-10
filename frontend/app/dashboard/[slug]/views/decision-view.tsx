"use client";

import { DecisionView } from "../../../../components/dashboard-views";
import { resolveSelectedSymbol } from "../../../../lib/selected-symbol";

import type { DashboardViewModuleProps, DecisionEntryFlowTab } from "../dashboard-view-types";

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

export function DecisionDashboardView({
  query,
  sections,
  operatorPayload,
}: DashboardViewModuleProps) {
  if (!operatorPayload) {
    return null;
  }

  const selectedSymbol = resolveSelectedSymbol(
    queryValue(query.symbol),
    operatorPayload.control.tracked_symbols,
    operatorPayload.control.default_symbol,
    { mode: "single" },
  );

  return (
    <DecisionView
      operator={operatorPayload}
      decisionRows={sections[0]?.rows ?? []}
      selectedSymbol={selectedSymbol}
      entryFlowTab={resolveDecisionEntryFlowTab(query.flow)}
    />
  );
}
