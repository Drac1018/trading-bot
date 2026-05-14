import assert from "node:assert/strict";
import test from "node:test";

type OrdersQueryModule = typeof import("./orders-query");

const ordersQueryModule = import(
  new URL("./orders-query.ts", import.meta.url).href,
) as Promise<OrdersQueryModule>;

test("resolveOrdersQuery keeps supported filters", async () => {
  const { resolveOrdersQuery } = await ordersQueryModule;

  assert.deepEqual(
    resolveOrdersQuery({
      tab: "executions",
      symbol: "ethusdt",
      position_id: "42",
    }),
    {
      tab: "executions",
      symbol: "ETHUSDT",
      positionId: 42,
    },
  );
});

test("resolveOrdersQuery drops unsupported tab and invalid position id", async () => {
  const { resolveOrdersQuery } = await ordersQueryModule;

  assert.deepEqual(
    resolveOrdersQuery({
      tab: "raw",
      symbol: " ",
      position_id: "12x",
    }),
    {
      tab: "summary",
      symbol: null,
      positionId: null,
    },
  );
});

test("resolveOrdersQuery accepts legacy trading page tab alias", async () => {
  const { resolveOrdersQuery } = await ordersQueryModule;

  assert.equal(resolveOrdersQuery({ ordersTab: "orders" }).tab, "orders");
});

test("ordersViewHref preserves tab, symbol, and position filter", async () => {
  const { ordersViewHref } = await ordersQueryModule;

  assert.equal(
    ordersViewHref({ tab: "orders", symbol: "btcusdt", positionId: 77 }),
    "/dashboard/orders?tab=orders&symbol=BTCUSDT&position_id=77",
  );
  assert.equal(ordersViewHref({ tab: "summary" }), "/dashboard/orders");
});

test("ordersDataEndpoints requests compact selected position payload", async () => {
  const { ordersDataEndpoints } = await ordersQueryModule;
  const [ordersEndpoint, executionsEndpoint] = ordersDataEndpoints({
    symbol: "solusdt",
    position_id: "91",
  });

  assert.equal(ordersEndpoint, "/api/orders?limit=120&compact=true&symbol=SOLUSDT&position_id=91");
  assert.equal(executionsEndpoint, "/api/executions?limit=120&compact=true&symbol=SOLUSDT&position_id=91");
});
