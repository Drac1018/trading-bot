import assert from "node:assert/strict";
import test from "node:test";

import type { EventOperatorControlPayload } from "./event-operator-control.js";

type SettingsEventPreviewModule = typeof import("./settings-event-preview");
type EventOperatorControlModule = typeof import("./event-operator-control");

const settingsEventPreviewModule = import(
  new URL("./settings-event-preview.ts", import.meta.url).href,
) as Promise<SettingsEventPreviewModule>;
const eventOperatorControlModule = import(
  new URL("./event-operator-control.ts", import.meta.url).href,
) as Promise<EventOperatorControlModule>;

function buildEventOperatorControl(
  overrides: Partial<EventOperatorControlPayload> = {},
): EventOperatorControlPayload {
  return {
    event_context: {
      source_status: "available",
      source_provenance: "fixture",
      generated_at: "2026-04-20T11:00:00Z",
      is_stale: false,
      is_complete: true,
      active_risk_window: false,
      next_event_at: "2026-04-20T12:30:00Z",
      next_event_name: "FOMC",
      next_event_importance: "high",
      minutes_to_next_event: 42,
      upcoming_events: [],
      affected_assets: ["BTCUSDT"],
      summary_note: "fixture event context",
    },
    ai_event_view: {
      ai_bias: "bullish",
      ai_risk_state: "risk_on",
      ai_confidence: 0.68,
      scenario_note: "Wait for confirmation.",
      confidence_penalty_reason: "EVENT_WINDOW_PROXIMITY",
      source_state: "available",
    },
    operator_event_view: {
      operator_bias: "neutral",
      operator_risk_state: "neutral",
      applies_to_symbols: ["BTCUSDT"],
      horizon: "event-day",
      valid_from: "2026-04-20T11:00:00Z",
      valid_to: "2026-04-20T13:00:00Z",
      enforcement_mode: "approval_required",
      note: "Wait for event resolution.",
      created_by: "operator-ui",
      updated_at: "2026-04-20T11:01:00Z",
    },
    alignment_decision: {
      ai_bias: "bullish",
      operator_bias: "neutral",
      ai_risk_state: "risk_on",
      operator_risk_state: "neutral",
      alignment_status: "partially_aligned",
      reason_codes: ["approval_required_preview"],
      effective_policy_preview: "allow_with_approval",
      evaluated_at: "2026-04-20T11:02:00Z",
    },
    manual_no_trade_windows: [],
    effective_policy_preview: "allow_with_approval",
    blocked_reason: null,
    degraded_reason: null,
    approval_required_reason: "alignment_not_aligned",
    policy_source: "alignment_policy",
    ...overrides,
  };
}

test("buildSettingsEventPreviewSummary returns user-facing summary copy for settings cards", async () => {
  const { buildSettingsEventPreviewSummary } = await settingsEventPreviewModule;
  const { describeSourceStatusHelp } = await eventOperatorControlModule;

  const summary = buildSettingsEventPreviewSummary(buildEventOperatorControl());

  assert.equal(
    summary.entryPolicySummary,
    "지금 신규 진입은 \"승인 후 가능\" 상태입니다. 이유: AI와 운영자 의견이 완전히 같지 않아 한 번 더 확인이 필요합니다. 기준: AI와 운영자 의견 비교.",
  );
  assert.equal(
    summary.alignmentReasonSummary,
    "현재 상태라면 신규 진입 전에 한 번 더 확인하는 것이 좋습니다.",
  );
  assert.equal(
    summary.eventSourceHelp,
    describeSourceStatusHelp("available", {
      kind: "event_context",
      provenance: "fixture",
    }),
  );
});

test("buildSettingsEventPreviewSummary keeps unavailable source and missing alignment visible", async () => {
  const { buildSettingsEventPreviewSummary } = await settingsEventPreviewModule;
  const { describeSourceStatusHelp } = await eventOperatorControlModule;

  const summary = buildSettingsEventPreviewSummary(
    buildEventOperatorControl({
      event_context: {
        source_status: "unavailable",
        source_provenance: "stub",
        generated_at: "2026-04-20T11:00:00Z",
        is_stale: false,
        is_complete: false,
        active_risk_window: false,
        next_event_at: null,
        next_event_name: null,
        next_event_importance: "unknown",
        minutes_to_next_event: null,
        upcoming_events: [],
        affected_assets: [],
        summary_note: "provider unavailable",
      },
      alignment_decision: {
        ai_bias: "unknown",
        operator_bias: "unknown",
        ai_risk_state: "unknown",
        operator_risk_state: "unknown",
        alignment_status: "insufficient_data",
        reason_codes: [],
        effective_policy_preview: "insufficient_data",
        evaluated_at: "2026-04-20T11:02:00Z",
      },
      effective_policy_preview: "insufficient_data",
      approval_required_reason: null,
      policy_source: "none",
    }),
  );

  assert.equal(
    summary.entryPolicySummary,
    "지금 신규 진입은 \"판단 보류\" 상태입니다. 이유: 추가 사유 없음. 기준: 추가 제한 없음.",
  );
  assert.equal(summary.alignmentReasonSummary, "추가 사유 없음");
  assert.equal(
    summary.eventSourceHelp,
    describeSourceStatusHelp("unavailable", {
      kind: "event_context",
      provenance: "stub",
    }),
  );
});
