"use client";

import { PositionsView } from "../../../../components/dashboard-views";

import type { DashboardViewModuleProps } from "../dashboard-view-types";

export function PositionsDashboardView({
  sections,
}: DashboardViewModuleProps) {
  return <PositionsView positionRows={sections[0]?.rows ?? []} />;
}
