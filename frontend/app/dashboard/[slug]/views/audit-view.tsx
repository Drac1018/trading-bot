"use client";

import { LogExplorer, type AuditRow } from "../../../../components/log-explorer";

import type { DashboardViewModuleProps } from "../dashboard-view-types";

export function AuditDashboardView({
  query,
  sections,
}: DashboardViewModuleProps) {
  const initialTab = typeof query.tab === "string" ? query.tab : "all";
  return (
    <LogExplorer
      initialRows={(sections[0]?.rows ?? []) as AuditRow[]}
      initialTab={initialTab}
      initialLimit={30}
    />
  );
}
