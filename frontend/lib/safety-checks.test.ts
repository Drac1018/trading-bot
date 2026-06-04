import assert from "node:assert/strict";
import test from "node:test";

import type { SafetyCheckRow } from "./safety-checks";

type SafetyChecksModule = typeof import("./safety-checks");

const safetyChecksModule = import(new URL("./safety-checks.ts", import.meta.url).href) as Promise<SafetyChecksModule>;

function expectedSeoulLabel(timestamp: string): string {
  return `${new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(timestamp))} KST`;
}

async function createdAtLabel(createdAt: SafetyCheckRow["created_at"]): Promise<string> {
  const { buildSafetyCheckView } = await safetyChecksModule;
  return buildSafetyCheckView({ id: "timestamp-test", created_at: createdAt }).createdAtLabel;
}

test("attachSafetyCheckAuditEventIds maps real audit events without inventing ids", async () => {
  const { attachSafetyCheckAuditEventIds } = await safetyChecksModule;

  const rows: SafetyCheckRow[] = [
    { id: 101, symbol: "BTCUSDT" },
    { id: 102, symbol: "ETHUSDT", audit_event_id: "existing" },
    { id: 103, symbol: "SOLUSDT" },
  ];

  const enriched = attachSafetyCheckAuditEventIds(rows, [
    { id: 9001, event_type: "risk_check", entity_id: "101" },
    { id: 9002, event_type: "decision_risk_blocked", entity_id: "run-1", payload: { risk_check_id: 103 } },
  ]);

  assert.equal(enriched[0]?.audit_event_id, "9001");
  assert.equal(enriched[1]?.audit_event_id, "existing");
  assert.equal(enriched[2]?.audit_event_id, "9002");
  assert.equal(attachSafetyCheckAuditEventIds([{ id: 104 }], [])[0]?.audit_event_id, undefined);
});

test("buildSafetyCheckSummaryView keeps the initial list to operator summary fields", async () => {
  const { buildSafetyCheckSummaryView } = await safetyChecksModule;

  const view = buildSafetyCheckSummaryView({
    id: 201,
    risk_check_id: 201,
    audit_event_id: 9001,
    has_audit_event: true,
    symbol: "ETHUSDT",
    decision: "short",
    intent: "short",
    allowed: false,
    blocked_reason_codes: ["MARKET_STATE_STALE"],
    created_at: "2026-05-10T01:02:03Z",
  });

  assert.equal(view.id, "201");
  assert.equal(view.symbol, "ETHUSDT");
  assert.equal(view.requestedAction, "숏 진입");
  assert.equal(view.intentLabel, "숏 진입");
  assert.equal(view.resultLabel, "차단");
  assert.equal(view.blockedReasonSummary, "시장 데이터가 오래됨");
  assert.equal(view.auditEventLabel, "감사 이벤트 #9001");
  assert.equal(view.hasAuditEvent, true);
});

test("buildSafetyCheckView presents an approved risk check in Korean", async () => {
  const { buildSafetyCheckView } = await safetyChecksModule;
  const row: SafetyCheckRow = {
    id: 101,
    symbol: "BTCUSDT",
    decision: "long",
    allowed: true,
    created_at: "2026-05-10T01:02:03Z",
    ai_trigger_summary: "range breakout",
    payload: {
      allowed: true,
      decision: "long",
      exposure_metrics: {
        gross_exposure_pct_equity: 12.5,
        largest_position_pct_equity: 4.5,
        directional_bias_pct: 8,
        same_tier_concentration_pct: 20,
        open_position_count: 2,
      },
      sync_freshness_summary: {
        account: { status: "synced", stale: false, incomplete: false },
      },
      effective_leverage_cap: 3,
    },
    risk_guard_result: {
      allowed: true,
      decision: "long",
      reason_codes: [],
      blocked_reason_codes: [],
    },
  };

  const view = buildSafetyCheckView(row);

  assert.equal(view.resultLabel, "승인");
  assert.equal(view.requestedAction, "롱 진입");
  assert.equal(view.marketDataStatus.label, "시장 데이터 확인됨");
  assert.equal(view.accountTrustStatus.label, "계좌 상태 신뢰 가능");
});

test("buildSafetyCheckView parses timezone-less API timestamps as UTC", async () => {
  const label = await createdAtLabel("2026-05-10T10:52:40.279462");

  assert.equal(label, expectedSeoulLabel("2026-05-10T10:52:40.279462Z"));
  assert.match(label, /19:52:40 KST/);
});

test("buildSafetyCheckView keeps Z suffix timestamps unchanged", async () => {
  assert.equal(
    await createdAtLabel("2026-05-10T10:52:40.279462Z"),
    expectedSeoulLabel("2026-05-10T10:52:40.279462Z"),
  );
});

test("buildSafetyCheckView keeps +00:00 suffix timestamps unchanged", async () => {
  assert.equal(
    await createdAtLabel("2026-05-10T10:52:40.279462+00:00"),
    expectedSeoulLabel("2026-05-10T10:52:40.279462Z"),
  );
});

test("buildSafetyCheckView keeps +09:00 suffix timestamps unchanged", async () => {
  assert.equal(
    await createdAtLabel("2026-05-10T19:52:40+09:00"),
    expectedSeoulLabel("2026-05-10T10:52:40Z"),
  );
});

test("buildSafetyCheckView keeps invalid and empty timestamp fallbacks", async () => {
  assert.equal(await createdAtLabel("not-a-timestamp"), "not-a-timestamp");
  assert.equal(await createdAtLabel(null), await createdAtLabel(undefined));
  assert.equal(await createdAtLabel(""), await createdAtLabel(undefined));
});

test("buildSafetyCheckView localizes blocked market stale reason", async () => {
  const { buildSafetyCheckView } = await safetyChecksModule;
  const row: SafetyCheckRow = {
    id: 102,
    symbol: "ETHUSDT",
    decision: "short",
    allowed: false,
    blocked_reason_codes: ["MARKET_STATE_STALE"],
    payload: {
      allowed: false,
      decision: "short",
      reason_codes: ["MARKET_STATE_STALE"],
      blocked_reason_codes: ["MARKET_STATE_STALE"],
      exposure_metrics: {},
    },
  };

  const view = buildSafetyCheckView(row);

  assert.equal(view.resultLabel, "차단");
  assert.equal(view.marketDataStatus.label, "시장 데이터가 오래됨");
  assert.deepEqual(view.reasonDisplays.map((reason) => reason.label), ["시장 데이터가 오래됨"]);
});

test("buildSafetyCheckView marks incomplete market data separately", async () => {
  const { buildSafetyCheckView } = await safetyChecksModule;
  const row: SafetyCheckRow = {
    id: 103,
    symbol: "SOLUSDT",
    decision: "hold",
    allowed: false,
    payload: {
      allowed: false,
      decision: "hold",
      reason_codes: ["MARKET_STATE_INCOMPLETE"],
      blocked_reason_codes: ["MARKET_STATE_INCOMPLETE"],
      exposure_metrics: {},
    },
  };

  const view = buildSafetyCheckView(row);

  assert.equal(view.marketDataStatus.label, "시장 데이터가 불완전함");
  assert.equal(view.reasonDisplays[0]?.code, "MARKET_STATE_INCOMPLETE");
});

test("buildSafetyCheckView summarizes daily drawdown state instead of dumping json", async () => {
  const { buildSafetyCheckView } = await safetyChecksModule;
  const row: SafetyCheckRow = {
    id: 105,
    symbol: "ETHUSDT",
    decision: "hold",
    allowed: false,
    payload: {
      allowed: false,
      decision: "hold",
      drawdown_state: {
        current_drawdown_state: "normal",
        previous_drawdown_state: "normal",
        state_changed: false,
        policy_adjustments: {
          risk_pct_multiplier: 1,
          leverage_multiplier: 1,
          notional_multiplier: 1,
          entry_capacity_multiplier: 1,
          entry_score_threshold_uplift: 0,
          winner_only_pyramiding: false,
        },
        drawdown_depth_pct: 0,
        recent_net_pnl: 0,
        consecutive_losses: 0,
        recovery_progress: 1,
        latest_pnl_snapshot_at: "2026-05-10T07:47:29.541108",
      },
      exposure_metrics: {},
    },
  };

  const view = buildSafetyCheckView(row);

  assert.equal(view.dailyLossStatus.label, "정상");
  assert.match(view.dailyLossStatus.detail ?? "", /낙폭 0%/);
  assert.match(view.dailyLossStatus.detail ?? "", /최근 순손익 0 USDT/);
  assert.match(view.dailyLossStatus.detail ?? "", /연속 손실 0회/);
  assert.match(view.dailyLossStatus.detail ?? "", /정책 조정 없음/);
  assert.doesNotMatch(view.dailyLossStatus.detail ?? "", /current_drawdown_state/);
  assert.equal(view.consecutiveLossStatus.detail, "0회");
});

test("buildSafetyCheckView exposes reduce-only and raw json without mutation controls", async () => {
  const { buildSafetyCheckView } = await safetyChecksModule;
  const row: SafetyCheckRow = {
    id: 104,
    symbol: "XRPUSDT",
    decision: "reduce",
    allowed: true,
    payload: {
      allowed: true,
      decision: "reduce",
      reduce_only_allowed: true,
      exposure_metrics: { gross_exposure_pct_equity: 33 },
    },
    risk_guard_result: {
      allowed: true,
      decision: "reduce",
      reduce_only_allowed: true,
    },
  };

  const view = buildSafetyCheckView(row);

  assert.equal(view.resultLabel, "reduce_only 허용");
  assert.match(view.rawJson, /XRPUSDT/);
  assert.doesNotMatch(view.rawJson, /approval arm/i);
});
