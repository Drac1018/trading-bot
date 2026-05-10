"use client";

import { OrdersView } from "../../../../components/dashboard-views";

import type { DashboardViewModuleProps, OrderLifecycleTab } from "../dashboard-view-types";

function queryValue(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

function resolveOrderLifecycleTab(value: string | string[] | undefined): OrderLifecycleTab {
  const normalized = queryValue(value);
  if (normalized === "orders" || normalized === "executions") {
    return normalized;
  }
  return "summary";
}

export function OrdersDashboardView({
  query,
  sections,
}: DashboardViewModuleProps) {
  return (
    <OrdersView
      orderRows={sections[0]?.rows ?? []}
      executionRows={sections[1]?.rows ?? []}
      activeTab={resolveOrderLifecycleTab(query.tab)}
    />
  );
}
