export type OrderLifecycleTab = "summary" | "orders" | "executions";

export type OrdersQueryState = {
  tab: OrderLifecycleTab;
  symbol: string | null;
  positionId: number | null;
};

type QueryValue = string | string[] | undefined | null;
type OrdersQueryInput = Record<string, QueryValue>;

function queryValue(value: QueryValue) {
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

export function resolveOrderLifecycleTab(value: QueryValue): OrderLifecycleTab {
  const normalized = queryValue(value);
  if (normalized === "orders" || normalized === "executions") {
    return normalized;
  }
  return "summary";
}

export function resolveOrderSymbol(value: QueryValue) {
  const normalized = queryValue(value)?.trim().toUpperCase();
  return normalized && normalized.length > 0 ? normalized : null;
}

export function resolveOrderPositionId(value: QueryValue) {
  const normalized = queryValue(value)?.trim();
  if (!normalized || !/^\d+$/.test(normalized)) {
    return null;
  }
  const parsed = Number.parseInt(normalized, 10);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

export function resolveOrdersQuery(query: OrdersQueryInput): OrdersQueryState {
  return {
    tab: resolveOrderLifecycleTab(query.tab ?? query.ordersTab),
    symbol: resolveOrderSymbol(query.symbol),
    positionId: resolveOrderPositionId(query.position_id),
  };
}

export function ordersViewHref({
  tab = "summary",
  symbol = null,
  positionId = null,
}: {
  tab?: OrderLifecycleTab | null;
  symbol?: string | null;
  positionId?: number | null;
} = {}) {
  const params = new URLSearchParams();
  if (tab && tab !== "summary") {
    params.set("tab", tab);
  }
  const normalizedSymbol = resolveOrderSymbol(symbol);
  if (normalizedSymbol) {
    params.set("symbol", normalizedSymbol);
  }
  if (positionId !== null && Number.isSafeInteger(positionId) && positionId > 0) {
    params.set("position_id", String(positionId));
  }
  const queryString = params.toString();
  return queryString ? `/dashboard/orders?${queryString}` : "/dashboard/orders";
}

export function ordersDataEndpoints(query: OrdersQueryInput) {
  const state = resolveOrdersQuery(query);
  const limit = state.positionId !== null ? 120 : state.symbol ? 80 : 40;

  const endpoint = (path: "/api/orders" | "/api/executions") => {
    const params = new URLSearchParams({
      limit: String(limit),
      compact: "true",
    });
    if (state.symbol) {
      params.set("symbol", state.symbol);
    }
    if (state.positionId !== null) {
      params.set("position_id", String(state.positionId));
    }
    return `${path}?${params.toString()}`;
  };

  return [endpoint("/api/orders"), endpoint("/api/executions")] as const;
}
