import type { ReactNode } from "react";
import { notFound } from "next/navigation";

import {
  AgentDebugView,
  DecisionView,
  MarketSignalView,
  SchedulerView,
} from "../../../components/dashboard-views";
import { DataTable } from "../../../components/data-table";
import { LogExplorer, type AuditRow } from "../../../components/log-explorer";
import { PageShell } from "../../../components/page-shell";
import { SettingsControls, type SettingsPayload } from "../../../components/settings-controls";
import { type OperatorDashboardPayload } from "../../../components/overview-dashboard";
import { fetchJson } from "../../../lib/api";
import { resolveSelectedSymbol } from "../../../lib/selected-symbol";
import { dashboardPages } from "../../../lib/page-config";

type Row = Record<string, unknown>;

function queryValue(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

export default async function DashboardPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { slug } = await params;
  const config = dashboardPages[slug];
  const query = await searchParams;

  if (!config) {
    notFound();
  }

  // Audit uses the shared explorer component with audit-only data.
  if (slug === "audit") {
    const auditRows = await fetchJson<AuditRow[]>("/api/audit?limit=100");
    const initialTab = typeof query.tab === "string" ? query.tab : "all";

    return <LogExplorer initialRows={auditRows} initialTab={initialTab} initialLimit={100} />;
  }

  const settingsPayload = slug === "settings" ? await fetchJson<SettingsPayload>("/api/settings") : null;
  const operatorPayload =
    slug === "market" || slug === "decisions" || slug === "scheduler"
      ? await fetchJson<OperatorDashboardPayload>("/api/dashboard/operator")
      : null;

  const sections = await Promise.all(
    config.sections.map(async (section) => ({
      ...section,
      rows: await fetchJson<Row[] | Row>(section.endpoint),
    })),
  );

  const normalizedSections = sections.map((section) => ({
    ...section,
    rows: Array.isArray(section.rows) ? section.rows : [section.rows],
  }));

  let content: ReactNode = null;

  if (slug === "market" && operatorPayload) {
    const selectedSymbol = resolveSelectedSymbol(
      queryValue(query.symbol),
      operatorPayload.control.tracked_symbols,
      operatorPayload.control.default_symbol,
      { mode: "all" },
    );
    content = (
      <MarketSignalView
        operator={operatorPayload}
        snapshots={normalizedSections[0]?.rows ?? []}
        features={normalizedSections[1]?.rows ?? []}
        selectedSymbol={selectedSymbol}
      />
    );
  } else if (slug === "decisions" && operatorPayload) {
    const selectedSymbol = resolveSelectedSymbol(
      queryValue(query.symbol),
      operatorPayload.control.tracked_symbols,
      operatorPayload.control.default_symbol,
      { mode: "single" },
    );
    content = (
      <DecisionView
        operator={operatorPayload}
        decisionRows={normalizedSections[0]?.rows ?? []}
        selectedSymbol={selectedSymbol}
      />
    );
  } else if (slug === "scheduler" && operatorPayload) {
    content = <SchedulerView operator={operatorPayload} schedulerRows={normalizedSections[0]?.rows ?? []} />;
  } else if (slug === "agents") {
    content = <AgentDebugView agentRows={normalizedSections[0]?.rows ?? []} />;
  } else {
    content = normalizedSections.map((section) => (
      <DataTable
        key={section.title}
        title={section.title}
        description={section.description}
        rows={section.rows}
      />
    ));
  }

  return (
    <div className="space-y-6">
      <PageShell eyebrow={config.eyebrow} title={config.title} description={config.description} />

      {settingsPayload ? <SettingsControls initial={settingsPayload} /> : null}

      {content}
    </div>
  );
}
