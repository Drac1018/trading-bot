"use client";

import { OrdersView } from "../../../../components/dashboard-views";
import { resolveOrdersQuery } from "../../../../lib/orders-query";

import type { DashboardViewModuleProps } from "../dashboard-view-types";

export function OrdersDashboardView({
  query,
  sections,
}: DashboardViewModuleProps) {
  const ordersQuery = resolveOrdersQuery(query);

  return (
    <OrdersView
      orderRows={sections[0]?.rows ?? []}
      executionRows={sections[1]?.rows ?? []}
      activeTab={ordersQuery.tab}
      selectedSymbol={ordersQuery.symbol}
      selectedPositionId={ordersQuery.positionId}
    />
  );
}
