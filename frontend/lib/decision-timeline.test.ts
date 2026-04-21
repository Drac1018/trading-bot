import assert from "node:assert/strict";
import test from "node:test";

import type { DecisionTimelineSymbolLike } from "./decision-timeline";

type DecisionTimelineModule = typeof import("./decision-timeline");

const decisionTimelineModule = import(
  new URL("./decision-timeline.ts", import.meta.url).href,
) as Promise<DecisionTimelineModule>;

function buildSymbol(
  overrides: Partial<DecisionTimelineSymbolLike> = {},
): DecisionTimelineSymbolLike {
  return {
    ai_decision: {
      decision: "long",
      decision_reference: {
        display_gap: false,
        display_gap_reason: null,
      },
      ...(overrides.ai_decision ?? {}),
    },
    risk_guard: {
      allowed: true,
      decision: "long",
      auto_resized_entry: false,
      adjustment_reason_codes: [],
      ...(overrides.risk_guard ?? {}),
    },
    execution: {
      order_id: null,
      order_status: null,
      execution_status: null,
      ...(overrides.execution ?? {}),
    },
    pending_entry_plan: {
      plan_id: null,
      plan_status: null,
      entry_mode: null,
      canceled_reason: null,
      ...(overrides.pending_entry_plan ?? {}),
    },
    candidate_selection: {
      selected: true,
      selection_reason: "ranked_portfolio_focus",
      selected_reason: "ranked_portfolio_focus",
      rejected_reason: null,
      ...(overrides.candidate_selection ?? {}),
    },
  };
}

test("historical AI snapshot is labeled separately from current cycle and execution", async () => {
  const {
    describeHistoricalDecisionGap,
    summarizeCurrentCycleSelection,
    summarizeExecutionState,
    summarizeLastAiRecommendation,
  } = await decisionTimelineModule;

  const symbol = buildSymbol({
    ai_decision: {
      decision: "long",
      decision_reference: {
        display_gap: true,
        display_gap_reason: "newer_market_refresh",
      },
    },
    candidate_selection: {
      selected: false,
      selection_reason: "breadth_hold_bias",
      selected_reason: null,
      rejected_reason: "breadth_hold_bias",
    },
  });

  const aiSummary = summarizeLastAiRecommendation(symbol);
  const currentCycle = summarizeCurrentCycleSelection(symbol);
  const execution = summarizeExecutionState(symbol);

  assert.equal(aiSummary.kind, "warn");
  assert.ok(aiSummary.label.includes("과거"));
  assert.equal(currentCycle.kind, "neutral");
  assert.ok(currentCycle.label.includes("현재 cycle"));
  assert.equal(execution.kind, "neutral");
  assert.ok(execution.detail.includes("마지막 AI 추천"));
  assert.ok(describeHistoricalDecisionGap(symbol)?.includes("현재 화면"));
});

test("pending entry plan is surfaced before execution is shown as missing", async () => {
  const { summarizeExecutionState } = await decisionTimelineModule;

  const symbol = buildSymbol({
    pending_entry_plan: {
      plan_id: 42,
      plan_status: "armed",
      entry_mode: "pullback_confirm",
      canceled_reason: null,
    },
  });

  const execution = summarizeExecutionState(symbol);

  assert.equal(execution.kind, "warn");
  assert.ok(execution.label.includes("진입 대기"));
  assert.ok(execution.detail.includes("조건"));
});

test("risk pass remains distinct from order submission", async () => {
  const { summarizeExecutionState, summarizeRiskGate } = await decisionTimelineModule;

  const symbol = buildSymbol({
    candidate_selection: {
      selected: true,
      selection_reason: "ranked_portfolio_focus",
      selected_reason: "ranked_portfolio_focus",
      rejected_reason: null,
    },
  });

  const risk = summarizeRiskGate(symbol);
  const execution = summarizeExecutionState(symbol);

  assert.equal(risk.kind, "good");
  assert.ok(risk.label.includes("주문 전"));
  assert.equal(execution.kind, "warn");
  assert.ok(execution.label.includes("주문 제출 전"));
});

test("legacy trigger reasons are marked separately from current runtime reasons", async () => {
  const { describeAiTriggerReason } = await decisionTimelineModule;

  const legacy = describeAiTriggerReason("open_position_recheck_due");
  const current = describeAiTriggerReason("entry_candidate_event");

  assert.equal(legacy.legacy, true);
  assert.ok(legacy.hint.includes("과거 정책 기록"));
  assert.equal(current.legacy, false);
  assert.ok(current.hint.includes("현재 정책"));
});
