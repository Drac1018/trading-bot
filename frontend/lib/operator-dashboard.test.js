import test from "node:test";
import assert from "node:assert/strict";

import { ALL_SYMBOLS, filterSymbolsBySelection, resolveSelectedSymbol } from "./selected-symbol.ts";

const operatorControlModule = import(new URL("./operator-control.ts", import.meta.url).href);

test("resolveSelectedSymbol defaults to ALL for multi-symbol view", () => {
  const selected = resolveSelectedSymbol(null, ["BTCUSDT", "ETHUSDT"], "BTCUSDT", { mode: "all" });
  assert.equal(selected, ALL_SYMBOLS);
});

test("resolveSelectedSymbol preserves valid symbol query", () => {
  const selected = resolveSelectedSymbol("ethusdt", ["BTCUSDT", "ETHUSDT"], "BTCUSDT", { mode: "all" });
  assert.equal(selected, "ETHUSDT");
});

test("resolveSelectedSymbol defaults to default_symbol for single-symbol detail views", () => {
  const selected = resolveSelectedSymbol(null, ["BTCUSDT", "ETHUSDT"], "ETHUSDT", { mode: "single" });
  assert.equal(selected, "ETHUSDT");
});

test("resolveSelectedSymbol falls back to first tracked symbol when default_symbol is missing", () => {
  const selected = resolveSelectedSymbol(null, ["BTCUSDT", "ETHUSDT"], "SOLUSDT", { mode: "single" });
  assert.equal(selected, "BTCUSDT");
});

test("resolveSelectedSymbol ignores ALL query for single-symbol detail views", () => {
  const selected = resolveSelectedSymbol("ALL", ["BTCUSDT", "ETHUSDT"], "ETHUSDT", { mode: "single" });
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

test("serviceGateDisplayReasonCodes prefers root causes over generic blockers", async () => {
  const { serviceGateDisplayReasonCodes } = await operatorControlModule;

  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: false,
      service_gate_blockers: ["recent_scheduler_non_success", "recent_health_errors"],
      service_gate_root_cause_codes: ["EXCHANGE_AUTH_PERMISSION_REJECTED"],
    }),
    ["EXCHANGE_AUTH_PERMISSION_REJECTED"],
  );
});

test("serviceGateDisplayReasonCodes keeps blockers when root cause is generic", async () => {
  const { serviceGateDisplayReasonCodes } = await operatorControlModule;

  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: false,
      service_gate_blockers: [
        "reconciliation_not_synced",
        "recent_scheduler_non_success",
        "recent_health_errors",
      ],
      service_gate_root_cause_codes: ["WORKFLOW_EXCEPTION"],
    }),
    [
      "WORKFLOW_EXCEPTION",
      "RECONCILIATION_UNSYNCED",
      "RECENT_SCHEDULER_NON_SUCCESS",
      "RECENT_HEALTH_ERRORS",
    ],
  );
});

test("serviceGateDisplayReasonCodes keeps blockers when generic root cause is mixed with specifics", async () => {
  const { serviceGateDisplayReasonCodes } = await operatorControlModule;

  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: false,
      service_gate_blockers: ["recent_scheduler_non_success", "recent_health_errors"],
      service_gate_root_cause_codes: ["WORKFLOW_EXCEPTION", "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE"],
    }),
    [
      "WORKFLOW_EXCEPTION",
      "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE",
      "RECENT_SCHEDULER_NON_SUCCESS",
      "RECENT_HEALTH_ERRORS",
    ],
  );
});

test("serviceGateDisplayReasonCodes keeps redis cache blockers operator-visible", async () => {
  const { serviceGateDisplayReasonCodes } = await operatorControlModule;

  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: false,
      service_gate_blockers: ["redis_cache_unavailable"],
      service_gate_root_cause_codes: ["REDIS_CACHE_UNAVAILABLE"],
    }),
    ["REDIS_CACHE_UNAVAILABLE"],
  );
  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: false,
      service_gate_blockers: [" redis_cache_unavailable "],
      service_gate_root_cause_codes: [],
    }),
    ["REDIS_CACHE_UNAVAILABLE"],
  );
});

test("serviceGateDisplayReasonCodes normalizes stale root-cause casing", async () => {
  const { serviceGateDisplayReasonCodes } = await operatorControlModule;

  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: false,
      service_gate_blockers: ["recent_scheduler_non_success"],
      service_gate_root_cause_codes: [
        " exchange_auth_permission_rejected ",
        "EXCHANGE_AUTH_PERMISSION_REJECTED",
      ],
    }),
    ["EXCHANGE_AUTH_PERMISSION_REJECTED"],
  );
});

test("serviceGateDisplayReasonCodes falls back to blockers for stale runtime payloads", async () => {
  const { serviceGateDisplayReasonCodes } = await operatorControlModule;

  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: false,
      service_gate_blockers: [
        " recent_scheduler_non_success ",
        "RECENT_SCHEDULER_NON_SUCCESS",
        " ",
      ],
      service_gate_root_cause_codes: [],
    }),
    ["RECENT_SCHEDULER_NON_SUCCESS"],
  );
  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: false,
      service_gate_blockers: [" active_pending_entry_plans "],
      service_gate_root_cause_codes: [],
    }),
    ["ACTIVE_PENDING_ENTRY_PLANS"],
  );
});

test("serviceGateDisplayReasonCodes keeps unknown publication visible with blockers", async () => {
  const { serviceGateDisplayReasonCodes } = await operatorControlModule;

  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: null,
      service_gate_blockers: ["recent_scheduler_non_success", "recent_health_errors"],
      service_gate_root_cause_codes: [],
    }),
    ["SERVICE_GATE_STATUS_UNKNOWN", "RECENT_SCHEDULER_NON_SUCCESS", "RECENT_HEALTH_ERRORS"],
  );
  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_blockers: [" recent_health_errors "],
      service_gate_root_cause_codes: [],
    }),
    ["SERVICE_GATE_STATUS_UNKNOWN", "RECENT_HEALTH_ERRORS"],
  );
});

test("serviceGateDisplayReasonCodes handles scalar stale runtime payload fields", async () => {
  const { serviceGateDisplayReasonCodes } = await operatorControlModule;

  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_blockers: "recent_scheduler_non_success",
      service_gate_root_cause_codes: " exchange_auth_permission_rejected ",
    }),
    ["SERVICE_GATE_STATUS_UNKNOWN", "EXCHANGE_AUTH_PERMISSION_REJECTED"],
  );
  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_blockers: " recent_health_errors ",
      service_gate_root_cause_codes: null,
    }),
    ["SERVICE_GATE_STATUS_UNKNOWN", "RECENT_HEALTH_ERRORS"],
  );
});

test("serviceGateDisplayReasonCodes preserves blocked gate when partial payload lacks reasons", async () => {
  const { serviceGateDisplayReasonCodes } = await operatorControlModule;

  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: false,
      service_gate_blockers: [],
      service_gate_root_cause_codes: [],
    }),
    ["SERVICE_GATE_BLOCKED"],
  );
});

test("serviceGateDisplayReasonCodes exposes unknown gate publication", async () => {
  const { serviceGateDisplayReasonCodes } = await operatorControlModule;

  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_blockers: [],
      service_gate_root_cause_codes: [],
    }),
    ["SERVICE_GATE_STATUS_UNKNOWN"],
  );
  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: null,
      service_gate_blockers: [],
      service_gate_root_cause_codes: [],
    }),
    ["SERVICE_GATE_STATUS_UNKNOWN"],
  );
});

test("serviceGateDisplayReasonCodes flags clear gate conflicts", async () => {
  const { serviceGateDisplayReasonCodes } = await operatorControlModule;

  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: true,
      service_gate_blockers: ["recent_scheduler_non_success"],
      service_gate_root_cause_codes: [" exchange_auth_permission_rejected "],
    }),
    ["SERVICE_GATE_STATUS_UNKNOWN", "EXCHANGE_AUTH_PERMISSION_REJECTED"],
  );
  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: true,
      service_gate_blockers: [" recent_health_errors "],
      service_gate_root_cause_codes: [],
    }),
    ["SERVICE_GATE_STATUS_UNKNOWN", "RECENT_HEALTH_ERRORS"],
  );
  assert.deepEqual(
    serviceGateDisplayReasonCodes({
      service_gate_gate_clear: true,
      service_gate_blockers: [],
      service_gate_root_cause_codes: [],
    }),
    [],
  );
});

test("serviceGateDetailSummary summarizes published service-gate detail rows", async () => {
  const { serviceGateDetailSummary } = await operatorControlModule;

  const summary = serviceGateDetailSummary({
    service_gate_gate_clear: false,
    service_gate_blockers: ["recent_health_errors"],
    service_gate_root_cause_codes: ["RECENT_HEALTH_ERRORS", "EXCHANGE_POSITION_MODE_UNCLEAR"],
    service_gate_counts: {
      recent_scheduler_non_success: 0,
      recent_health_errors: 4,
    },
    service_gate_recent_scheduler_non_success: [],
    service_gate_recent_health_errors: [
      {
        workflow: "user_stream",
        status: "error",
        reason_code: "RECENT_HEALTH_ERRORS",
      },
      {
        workflow: "live_sync",
        status: "degraded",
        reason_code: "EXCHANGE_POSITION_MODE_UNCLEAR",
      },
    ],
    service_gate_redis_cache: { blocking: false },
  });

  assert.match(summary, /scheduler 0, health 4/);
  assert.match(summary, /health user_stream\/error\/RECENT_HEALTH_ERRORS/);
  assert.match(summary, /health live_sync\/degraded\/EXCHANGE_POSITION_MODE_UNCLEAR/);
});

test("serviceGateDetailSummary falls back to reason codes for stale payloads", async () => {
  const { serviceGateDetailSummary } = await operatorControlModule;

  assert.equal(
    serviceGateDetailSummary({
      service_gate_gate_clear: false,
      service_gate_blockers: ["recent_health_errors"],
      service_gate_root_cause_codes: ["EXCHANGE_POSITION_MODE_UNCLEAR"],
    }),
    "service-gate reasons: EXCHANGE_POSITION_MODE_UNCLEAR",
  );
});

test("serviceGateDetailSummary keeps empty detail rows readable", async () => {
  const { serviceGateDetailSummary } = await operatorControlModule;

  const summary = serviceGateDetailSummary({
    service_gate_gate_clear: false,
    service_gate_blockers: ["recent_scheduler_non_success"],
    service_gate_root_cause_codes: ["RECENT_SCHEDULER_NON_SUCCESS"],
    service_gate_counts: {
      recent_scheduler_non_success: 1,
      recent_health_errors: 0,
    },
    service_gate_recent_scheduler_non_success: [{}],
    service_gate_recent_health_errors: [],
  });

  assert.equal(
    summary,
    "service-gate detail: scheduler 1, health 0; scheduler detail unavailable",
  );
});
