"use client";

import { LogExplorer, type AuditRow } from "../../../../components/log-explorer";
import { parseAuditLimit, parseAuditSort } from "../../../../lib/audit-log";

import type { DashboardViewModuleProps } from "../dashboard-view-types";

export function AuditDashboardView({
  query,
  sections,
}: DashboardViewModuleProps) {
  const initialTab = typeof query.tab === "string" ? query.tab : "all";
  const initialSeverity = typeof query.severity === "string" ? query.severity : "";
  const initialSearch = typeof query.q === "string" ? query.q : typeof query.search === "string" ? query.search : "";
  const initialSort = parseAuditSort(typeof query.sort === "string" ? query.sort : null);
  const initialLimit = parseAuditLimit(typeof query.limit === "string" ? query.limit : null, 30);
  return (
    <LogExplorer
      initialRows={(sections[0]?.rows ?? []) as AuditRow[]}
      initialTab={initialTab}
      initialSeverity={initialSeverity}
      initialSearch={initialSearch}
      initialSort={initialSort}
      initialLimit={initialLimit}
    />
  );
}
