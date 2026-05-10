"use client";

import { AgentDebugView } from "../../../../components/dashboard-views";

import type { DashboardViewModuleProps } from "../dashboard-view-types";

export function AgentsDashboardView({
  sections,
}: DashboardViewModuleProps) {
  return <AgentDebugView agentRows={sections[0]?.rows ?? []} />;
}
