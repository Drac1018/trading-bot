"use client";

import { SchedulerView } from "../../../../components/dashboard-views";

import type { DashboardViewModuleProps } from "../dashboard-view-types";

export function SchedulerDashboardView({
  sections,
  operatorPayload,
}: DashboardViewModuleProps) {
  if (!operatorPayload) {
    return null;
  }

  return <SchedulerView operator={operatorPayload} schedulerRows={sections[0]?.rows ?? []} />;
}
