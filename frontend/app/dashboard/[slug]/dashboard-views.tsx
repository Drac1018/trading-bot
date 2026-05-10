import { DataTable } from "../../../components/data-table";

import type { NormalizedDashboardSection } from "./dashboard-view-types";

export function GenericDashboardSections({
  sections,
}: {
  sections: NormalizedDashboardSection[];
}) {
  return sections.map((section) => (
    <DataTable
      key={section.title}
      title={section.title}
      description={section.description}
      rows={section.rows}
    />
  ));
}
