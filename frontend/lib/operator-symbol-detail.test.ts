import assert from "node:assert/strict";
import test from "node:test";

import type { OperatorDetailSymbolLike } from "./operator-symbol-detail";

type OperatorSymbolDetailModule = typeof import("./operator-symbol-detail");
type EventOperatorControlModule = typeof import("./event-operator-control");

const operatorSymbolDetailModule = import(
  new URL("./operator-symbol-detail.ts", import.meta.url).href,
) as Promise<OperatorSymbolDetailModule>;
const eventOperatorControlModule = import(
  new URL("./event-operator-control.ts", import.meta.url).href,
) as Promise<EventOperatorControlModule>;

function buildSymbol(overrides: Record<string, unknown> = {}): OperatorDetailSymbolLike {
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
      source_provenance: "stub",
      next_event_name: "FOMC",
      next_event_at: "2026-04-20T12:30:00Z",
      next_event_importance: "high",
      minutes_to_next_event: 42,
      active_risk_window: false,
      is_stale: false,
      is_complete: true,
    },
    event_operator_control: {
      event_context: {
        source_status: "available",
        source_provenance: "fixture",
        generated_at: "2026-04-20T11:00:00Z",
        is_stale: false,
        is_complete: true,
        active_risk_window: false,
        active_risk_window_detail: null,
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
        scenario_note: "Prefer confirmation after the event before fresh entry.",
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
      operator_event_view_configured: true,
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
      evaluated_operator_policy: {
        operator_view_active: true,
        matched_window_id: null,
        alignment_status: "partially_aligned",
        enforcement_mode: "approval_required",
        reason_codes: ["approval_required_preview"],
        effective_policy_preview: "allow_with_approval",
        event_source_status: "available",
        event_source_stale: false,
        evaluated_at: "2026-04-20T11:02:00Z",
      },
      blocked_reason: null,
      degraded_reason: null,
      approval_required_reason: "alignment_not_aligned",
      policy_source: "alignment_policy",
      manual_no_trade_windows: [],
      effective_policy_preview: "allow_with_approval",
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
      blocked_reason_codes: ["alignment_not_aligned"],
      blocked_reason: null,
      degraded_reason: null,
      approval_required_reason: "alignment_not_aligned",
      policy_source: "alignment_policy",
    },
    execution: {
      order_id: null,
      execution_status: null,
      order_status: null,
    },
    blocked_reasons: [],
    stale_flags: [],
    ...overrides,
  } as OperatorDetailSymbolLike;
}

test("buildOperatorDetailSections keeps the additive event/operator sections in stable order", async () => {
  const { buildOperatorDetailSections } = await operatorSymbolDetailModule;
  const { describeEventSourceProvenance } = await eventOperatorControlModule;

  const sections = buildOperatorDetailSections(buildSymbol());
  const eventSection = sections.find((section) => section.key === "upcoming_event_risk");

  assert.deepEqual(
    sections.map((section) => section.key),
    [
      "current_regime",
      "derivatives_orderbook",
      "upcoming_event_risk",
      "ai_event_view",
      "operator_event_view",
      "alignment_result",
      "effective_trading_policy_preview",
      "manual_no_trade_window",
      "risk_guard_decision",
      "blocked_degraded_reason",
    ],
  );
  assert.deepEqual(
    sections.map((section) => section.title),
    [
      "현재 레짐",
      "파생 / 오더북",
      "예정 이벤트 리스크",
      "AI 이벤트 뷰",
      "운영자 이벤트 뷰",
      "정렬 결과",
      "신규 진입 정책 미리보기",
      "수동 노트레이드 윈도우",
      "리스크 가드 판정",
      "차단 / 저하 상태",
    ],
  );
  assert.equal(
    eventSection?.items.find((item) => item.label === "데이터 출처")?.value,
    describeEventSourceProvenance("fixture"),
  );
});

test("buildOperatorDetailSections exposes user-facing preview text", async () => {
  const { buildOperatorDetailSections } = await operatorSymbolDetailModule;

  const sections = buildOperatorDetailSections(buildSymbol());
  const previewSection = sections.find((section) => section.key === "effective_trading_policy_preview");

  assert.ok(previewSection);
  assert.equal(previewSection.tone, "warn");
  assert.ok(previewSection.items.some((item) => item.value === "승인 후 가능"));
  assert.ok(previewSection.items.some((item) => item.label === "신규 진입 한 줄 요약"));
  assert.ok(previewSection.alerts.some((alert) => alert.text.includes("실제 신규 진입 판단도 같은 기준")));
});

test("buildOperatorDetailSections keeps unavailable source visible in plain language", async () => {
  const { buildOperatorDetailSections } = await operatorSymbolDetailModule;
  const { describeEventSourceProvenance, describeSourceStatus, describeSourceStatusHelp } =
    await eventOperatorControlModule;

  const sections = buildOperatorDetailSections(
    buildSymbol({
      event_operator_control: {
        event_context: {
          source_status: "unavailable",
          source_provenance: "stub",
          generated_at: "2026-04-20T11:00:00Z",
          is_stale: false,
          is_complete: false,
          active_risk_window: false,
          active_risk_window_detail: null,
          next_event_at: null,
          next_event_name: null,
          next_event_importance: "unknown",
          minutes_to_next_event: null,
          upcoming_events: [],
          affected_assets: [],
          summary_note: "provider unavailable",
        },
        ai_event_view: {
          ai_bias: "unknown",
          ai_risk_state: "unknown",
          ai_confidence: null,
          scenario_note: null,
          confidence_penalty_reason: null,
          source_state: "unavailable",
        },
        operator_event_view: {
          operator_bias: "unknown",
          operator_risk_state: "unknown",
          applies_to_symbols: [],
          horizon: null,
          valid_from: null,
          valid_to: null,
          enforcement_mode: "observe_only",
          note: null,
          created_by: "unknown",
          updated_at: null,
        },
        operator_event_view_configured: false,
        alignment_decision: {
          ai_bias: "unknown",
          operator_bias: "unknown",
          ai_risk_state: "unknown",
          operator_risk_state: "unknown",
          alignment_status: "insufficient_data",
          reason_codes: ["ai_unavailable", "operator_unavailable"],
          effective_policy_preview: "insufficient_data",
          evaluated_at: "2026-04-20T11:02:00Z",
        },
        manual_no_trade_windows: [],
        effective_policy_preview: "insufficient_data",
      },
      stale_flags: ["feature_input_missing"],
      risk_guard: {
        allowed: null,
        decision: "hold",
        operating_state: "TRADABLE",
        approved_risk_pct: null,
        approved_leverage: null,
        blocked_reason_codes: [],
      },
    }),
  );

  const eventSection = sections.find((section) => section.key === "upcoming_event_risk");
  const operatorSection = sections.find((section) => section.key === "operator_event_view");
  const blockedSection = sections.find((section) => section.key === "blocked_degraded_reason");

  assert.ok(eventSection);
  assert.equal(eventSection.tone, "neutral");
  assert.equal(
    eventSection.items.find((item) => item.label === "데이터 상태")?.value,
    describeSourceStatus("unavailable", { kind: "event_context" }),
  );
  assert.equal(
    eventSection.items.find((item) => item.label === "데이터 출처")?.value,
    describeEventSourceProvenance("stub"),
  );
  assert.ok(operatorSection);
  assert.equal(operatorSection.tone, "warn");
  assert.ok(
    operatorSection.alerts.some((alert) =>
      alert.text.includes("운영자 이벤트 설정이 아직 없습니다."),
    ),
  );
  assert.ok(blockedSection);
  assert.ok(
    blockedSection.alerts.some((alert) =>
      alert.text.includes(
        describeSourceStatusHelp("unavailable", {
          kind: "event_context",
          provenance: "stub",
        }),
      ),
    ),
  );
});

test("buildOperatorDetailSections keeps manual no-trade windows visible with active state", async () => {
  const { buildOperatorDetailSections } = await operatorSymbolDetailModule;

  const sections = buildOperatorDetailSections(
    buildSymbol({
      event_operator_control: {
        ...buildSymbol().event_operator_control,
        manual_no_trade_windows: [
          {
            window_id: "ntw_preview",
            scope: { scope_type: "symbols", symbols: ["BTCUSDT"] },
            start_at: "2026-04-20T11:30:00Z",
            end_at: "2026-04-20T13:30:00Z",
            reason: "manual no-trade around event window",
            auto_resume: true,
            require_manual_rearm: false,
            created_by: "operator-ui",
            updated_at: "2026-04-20T11:31:00Z",
            is_active: true,
          },
        ],
        effective_policy_preview: "force_no_trade_window",
        alignment_decision: {
          ai_bias: "bullish",
          operator_bias: "neutral",
          ai_risk_state: "risk_on",
          operator_risk_state: "neutral",
          alignment_status: "partially_aligned",
          reason_codes: ["manual_no_trade_active"],
          effective_policy_preview: "force_no_trade_window",
          evaluated_at: "2026-04-20T11:32:00Z",
        },
      },
    }),
  );

  const windowSection = sections.find((section) => section.key === "manual_no_trade_window");
  const previewSection = sections.find((section) => section.key === "effective_trading_policy_preview");

  assert.ok(windowSection);
  assert.equal(windowSection.tone, "danger");
  assert.ok(windowSection.alerts.some((alert) => alert.text.includes("manual no-trade around event window")));
  assert.ok(previewSection);
  assert.equal(previewSection.tone, "danger");
  assert.ok(previewSection.items.some((item) => item.value === "신규 진입 금지"));
});
