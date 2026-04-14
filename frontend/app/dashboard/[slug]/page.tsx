import { notFound } from "next/navigation";

import { BacklogBoard, type BacklogBoardPayload } from "../../../components/backlog-board";
import { DataTable } from "../../../components/data-table";
import { LogExplorer, type AuditRow } from "../../../components/log-explorer";
import { PageShell } from "../../../components/page-shell";
import { SettingsControls, type SettingsPayload } from "../../../components/settings-controls";
import { fetchJson } from "../../../lib/api";
import { dashboardPages } from "../../../lib/page-config";

export default async function DashboardPage({
  params,
  searchParams
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

  if (slug === "backlog") {
    const board = await fetchJson<BacklogBoardPayload>("/api/backlog");

    return (
      <div className="space-y-6">
        <PageShell eyebrow={config.eyebrow} title={config.title} description={config.description} />
        <BacklogBoard initial={board} />
      </div>
    );
  }

  if (slug === "audit") {
    const auditRows = await fetchJson<AuditRow[]>("/api/audit?limit=100");
    const initialTab = typeof query.tab === "string" ? query.tab : "all";

    return <LogExplorer initialRows={auditRows} initialTab={initialTab} initialLimit={100} />;
  }

  const sections = await Promise.all(
    config.sections.map(async (section) => ({
      ...section,
      rows: await fetchJson<Record<string, unknown>[] | Record<string, unknown>>(section.endpoint)
    }))
  );

  const settingsPayload =
    slug === "settings"
      ? await fetchJson<SettingsPayload>("/api/settings")
      : null;

  return (
    <div className="space-y-6">
      <PageShell eyebrow={config.eyebrow} title={config.title} description={config.description} />

      {settingsPayload ? <SettingsControls initial={settingsPayload} /> : null}

      {sections.map((section) => {
        const rows = Array.isArray(section.rows) ? section.rows : [section.rows];
        return (
          <DataTable
            key={section.title}
            title={section.title}
            description={section.description}
            rows={rows}
          />
        );
      })}
    </div>
  );
}
