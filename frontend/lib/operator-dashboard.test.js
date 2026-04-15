import test from "node:test";
import assert from "node:assert/strict";

import { ALL_SYMBOLS, filterSymbolsBySelection, resolveSelectedSymbol } from "./operator-dashboard.ts";

test("resolveSelectedSymbol defaults to ALL for multi-symbol view", () => {
  const selected = resolveSelectedSymbol(null, ["BTCUSDT", "ETHUSDT"], "BTCUSDT");
  assert.equal(selected, ALL_SYMBOLS);
});

test("resolveSelectedSymbol preserves valid symbol query", () => {
  const selected = resolveSelectedSymbol("ethusdt", ["BTCUSDT", "ETHUSDT"], "BTCUSDT");
  assert.equal(selected, "ETHUSDT");
});

test("filterSymbolsBySelection returns all rows in ALL mode", () => {
  const rows = filterSymbolsBySelection(
    [{ symbol: "BTCUSDT" }, { symbol: "ETHUSDT" }],
    ALL_SYMBOLS,
  );
  assert.equal(rows.length, 2);
});

test("filterSymbolsBySelection returns only the selected symbol row", () => {
  const rows = filterSymbolsBySelection(
    [{ symbol: "BTCUSDT" }, { symbol: "ETHUSDT" }],
    "BTCUSDT",
  );
  assert.deepEqual(rows, [{ symbol: "BTCUSDT" }]);
});
