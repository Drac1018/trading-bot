import assert from "node:assert/strict";
import test from "node:test";

type DataTableModule = typeof import("./data-table");

const dataTableModule = import(new URL("./data-table.ts", import.meta.url).href) as Promise<DataTableModule>;

test("collectTableColumns unions keys across every row instead of the first row only", async () => {
  const { collectTableColumns } = await dataTableModule;

  const columns = collectTableColumns([
    { symbol: "BTCUSDT", status: "ok" },
    { symbol: "ETHUSDT", confidence: 0.71, created_at: "2026-04-16T00:00:00Z" },
  ]);

  assert.deepEqual(columns, ["status", "symbol", "confidence", "created_at"]);
});

test("splitTableColumns keeps late-appearing payload columns in detail output", async () => {
  const { splitTableColumns } = await dataTableModule;

  const columns = splitTableColumns(
    [
      { symbol: "BTCUSDT", status: "ok" },
      { symbol: "ETHUSDT", payload: { side: "long" }, created_at: "2026-04-16T00:00:00Z" },
    ],
    new Set<string>(),
  );

  assert.deepEqual(columns.primary, ["status", "symbol", "created_at"]);
  assert.deepEqual(columns.detail, ["payload"]);
});

test("buildTableRowKeys prefers stable identity fields over array index", async () => {
  const { buildTableRowKeys } = await dataTableModule;

  const keys = buildTableRowKeys([
    { order_id: "ord-1", symbol: "BTCUSDT" },
    { symbol: "ETHUSDT", timeframe: "15m", created_at: "2026-04-16T00:01:00Z" },
  ]);

  assert.deepEqual(keys, [
    "order_id:ord-1",
    "symbol:ETHUSDT|timeframe:15m|created_at:2026-04-16T00:01:00Z",
  ]);
});

test("buildTableRowKeys keeps duplicate fallback identities unique", async () => {
  const { buildTableRowKeys } = await dataTableModule;

  const keys = buildTableRowKeys([
    { message: "same", severity: "info" },
    { message: "same", severity: "info" },
  ]);

  assert.equal(keys[0], 'fallback:[["message","same"],["severity","info"]]');
  assert.equal(keys[1], 'fallback:[["message","same"],["severity","info"]]#2');
});
