"use client";

import { RiskView } from "../../../../components/dashboard-views";

import type { DashboardViewModuleProps } from "../dashboard-view-types";

export function RiskDashboardView({
  sections,
  operatorPayload,
}: DashboardViewModuleProps) {
  return (
    <RiskView
      operator={operatorPayload}
      riskRows={sections[0]?.rows ?? []}
      alertRows={sections[1]?.rows ?? []}
    />
  );
}
