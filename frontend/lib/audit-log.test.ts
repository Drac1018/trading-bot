import assert from "node:assert/strict";
import test from "node:test";

import type { AuditRow } from "./audit-log";

type AuditLogModule = typeof import("./audit-log");

const auditLogModule = import(new URL("./audit-log.ts", import.meta.url).href) as Promise<AuditLogModule>;

const rows: AuditRow[] = [
  {
    event_category: "risk",
    event_type: "risk_blocked",
    severity: "warning",
    message: "Risk guard blocked the entry.",
    created_at: "2026-04-15T12:05:00Z"
  },
  {
    event_category: "execution",
    event_type: "live_execution_rejected",
    severity: "error",
    message: "Execution rejected by exchange.",
    created_at: "2026-04-15T12:03:00Z"
  },
  {
    event_category: "approval_control",
    event_type: "trading_paused",
    severity: "info",
    message: "Trading paused manually.",
    created_at: "2026-04-15T12:01:00Z"
  }
];

test("parseAuditTab falls back to all for unknown values", async () => {
  const { parseAuditTab } = await auditLogModule;

  assert.equal(parseAuditTab("execution"), "execution");
  assert.equal(parseAuditTab("unknown"), "all");
  assert.equal(parseAuditTab(undefined), "all");
});

test("getAuditEventCategory and counts stay deterministic", async () => {
  const { getAuditEventCategory, getAuditTabCounts } = await auditLogModule;

  assert.equal(getAuditEventCategory(rows[0]), "risk");
  assert.equal(getAuditEventCategory({ event_type: "unknown" }), "health_system");
  assert.equal(getAuditEventCategory({ event_category: "future_category" }), "health_system");

  const counts = getAuditTabCounts(rows);
  assert.equal(counts.get("all"), 3);
  assert.equal(counts.get("risk"), 1);
  assert.equal(counts.get("execution"), 1);
  assert.equal(counts.get("approval_control"), 1);
});

test("filterAuditRows applies tab, severity, search and sort together", async () => {
  const { filterAuditRows } = await auditLogModule;

  const executionRows = filterAuditRows(rows, {
    activeTab: "execution",
    severityFilter: "",
    searchFilter: "",
    sortMode: "newest"
  });
  assert.deepEqual(executionRows.map((row) => row.event_type), ["live_execution_rejected"]);

  const searched = filterAuditRows(rows, {
    activeTab: "all",
    severityFilter: "warning",
    searchFilter: "blocked",
    sortMode: "severity"
  });
  assert.deepEqual(searched.map((row) => row.event_type), ["risk_blocked"]);
});

test("describeAuditLegacyReview marks legacy time-based trigger rows separately", async () => {
  const { describeAuditLegacyReview, extractLegacyReviewTriggerReason } = await auditLogModule;

  const row: AuditRow = {
    event_category: "ai_decision",
    event_type: "decision_review_skipped",
    created_at: "2026-04-15T12:10:00Z",
    payload: {
      trigger: {
        trigger_reason: "periodic_backstop_due",
      },
    },
  };

  assert.equal(extractLegacyReviewTriggerReason(row), "periodic_backstop_due");

  const presentation = describeAuditLegacyReview(row);
  assert.equal(presentation?.badge, "과거 정책 기록");
  assert.equal(presentation?.rawTriggerReason, "periodic_backstop_due");
  assert.equal(presentation?.legacy, true);
});

test("describeAuditLegacyReview ignores current runtime trigger reasons", async () => {
  const { describeAuditLegacyReview, extractLegacyReviewTriggerReason } = await auditLogModule;

  const row: AuditRow = {
    event_category: "ai_decision",
    event_type: "decision_review_requested",
    created_at: "2026-04-15T12:11:00Z",
    metadata_json: {
      ai_trigger: {
        trigger_reason: "entry_candidate_event",
      },
    },
  };

  assert.equal(extractLegacyReviewTriggerReason(row), null);
  assert.equal(describeAuditLegacyReview(row), null);
});
