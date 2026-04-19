import assert from "node:assert/strict";
import test from "node:test";

type OperatorSymbolDetailModule = typeof import("./operator-symbol-detail");

const operatorSymbolDetailModule = import(new URL("./operator-symbol-detail.ts", import.meta.url).href) as Promise<OperatorSymbolDetailModule>;

function buildSymbol(overrides: Record<string, unknown> = {}) {
  return {
    market_context_summary: {
      primary_regime: "bullish",
      trend_alignment: "bullish_aligned",
      volatility_regime: "normal",
      volume_regime: "strong",
      momentum_state: "strengthening",
    },
    derivatives_summary: {
      available: true,
      source: "binance_public",
      funding_bias: "neutral",
      basis_bias: "bullish",
      taker_flow_alignment: "bullish",
      spread_bps: 3.2,
      spread_stress: false,
      crowded_long_risk: false,
      crowded_short_risk: false,
    },
    event_context_summary: {
      source_status: "stub",
      next_event_name: "FOMC",
      next_event_at: "2026-04-20T12:30:00Z",
      next_event_importance: "high",
      minutes_to_next_event: 42,
      active_risk_window: false,
      event_bias: "neutral",
      is_stale: false,
      is_complete: true,
    },
    ai_decision: {
      decision: "long",
      confidence: 0.68,
      event_risk_acknowledgement: "High-impact macro event is approaching.",
      confidence_penalty_reason: "EVENT_WINDOW_PROXIMITY",
      scenario_note: "Prefer confirmation after the event before fresh entry.",
    },
    risk_guard: {
      allowed: false,
      decision: "long",
      operating_state: "TRADABLE",
      approved_risk_pct: 0.01,
      approved_leverage: 2,
      blocked_reason_codes: ["HIGH_IMPACT_EVENT_WINDOW"],
    },
    execution: {
      order_id: null,
      execution_status: null,
      order_status: null,
    },
    blocked_reasons: [],
    stale_flags: [],
    ...overrides,
  };
}

test("buildOperatorDetailSections exposes the operator sections in stable order", async () => {
  const { buildOperatorDetailSections } = await operatorSymbolDetailModule;

  const sections = buildOperatorDetailSections(buildSymbol());

  assert.deepEqual(
    sections.map((section) => section.key),
    [
      "current_regime",
      "derivatives_orderbook",
      "event_risk_context",
      "ai_event_rationale",
      "risk_guard_decision",
      "blocked_degraded_reason",
    ],
  );
});

test("buildOperatorDetailSections keeps blocked_reason visible in the blocked/degraded section", async () => {
  const { buildOperatorDetailSections } = await operatorSymbolDetailModule;

  const sections = buildOperatorDetailSections(
    buildSymbol({
      risk_guard: {
        allowed: false,
        decision: "long",
        operating_state: "TRADABLE",
        approved_risk_pct: 0.0,
        approved_leverage: 0.0,
        blocked_reason_codes: ["HIGH_IMPACT_EVENT_WINDOW"],
      },
    }),
  );
  const blockedSection = sections.find((section) => section.key === "blocked_degraded_reason");

  assert.ok(blockedSection);
  assert.equal(blockedSection.tone, "danger");
  assert.ok(blockedSection.alerts.some((alert) => alert.text.includes("HIGH_IMPACT_EVENT_WINDOW")));
});

test("buildOperatorDetailSections shows source unavailable explicitly in event risk context", async () => {
  const { buildOperatorDetailSections } = await operatorSymbolDetailModule;

  const sections = buildOperatorDetailSections(
    buildSymbol({
      event_context_summary: {
        source_status: "unavailable",
        active_risk_window: false,
        is_stale: false,
        is_complete: false,
      },
      risk_guard: {
        allowed: null,
        decision: "hold",
        operating_state: "TRADABLE",
        approved_risk_pct: null,
        approved_leverage: null,
        blocked_reason_codes: [],
      },
      ai_decision: {
        decision: "hold",
        confidence: 0.42,
        event_risk_acknowledgement: null,
        confidence_penalty_reason: null,
        scenario_note: null,
      },
      blocked_reasons: [],
      stale_flags: ["feature_input_missing"],
    }),
  );
  const eventSection = sections.find((section) => section.key === "event_risk_context");
  const degradedSection = sections.find((section) => section.key === "blocked_degraded_reason");

  assert.ok(eventSection);
  assert.equal(eventSection.tone, "warn");
  assert.equal(eventSection.items.find((item) => item.label === "소스 상태")?.value, "소스 없음");
  assert.ok(degradedSection);
  assert.ok(degradedSection.alerts.some((alert) => alert.text.includes("이벤트 컨텍스트: 소스 없음")));
});

test("buildOperatorDetailSections exposes AI event rationale and stays null-safe", async () => {
  const { buildOperatorDetailSections } = await operatorSymbolDetailModule;

  const populatedSections = buildOperatorDetailSections(buildSymbol());
  const populatedRationale = populatedSections.find((section) => section.key === "ai_event_rationale");

  assert.ok(populatedRationale);
  assert.equal(populatedRationale.tone, "warn");
  assert.equal(
    populatedRationale.items.find((item) => item.value === "EVENT_WINDOW_PROXIMITY")?.value,
    "EVENT_WINDOW_PROXIMITY",
  );

  const emptySections = buildOperatorDetailSections(
    buildSymbol({
      ai_decision: {
        decision: "hold",
        confidence: 0.31,
        event_risk_acknowledgement: null,
        confidence_penalty_reason: null,
        scenario_note: null,
      },
    }),
  );
  const emptyRationale = emptySections.find((section) => section.key === "ai_event_rationale");

  assert.ok(emptyRationale);
  assert.equal(emptyRationale.tone, "neutral");
  assert.ok(emptyRationale.alerts.some((alert) => alert.text.includes("AI event-aware")));
});
