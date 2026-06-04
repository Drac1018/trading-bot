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
      ai_review: {
        review_type: "entry_candidate_review",
        trigger_reason: "entry_candidate_event",
        trigger_reason_codes: ["ENTRY_CANDIDATE_SELECTED"],
        skip_reason: null,
        provider_status: "invoked",
        provider_invoked: true,
        provider_skipped: false,
        invoked_at: "2026-04-20T11:02:00Z",
        provider_name: "openai",
      },
      market_signal_summary: "모멘텀 강화 / 거래량 strong / 추세 상승 정렬",
      market_signal_context: {
        momentum_state: "strengthening",
        volume_regime: "strong",
        trend_alignment: "bullish_aligned",
        data_quality_flags: [],
      },
      macro_event_risk_summary: {
        source_status: "available",
        source_vendor: "fred",
        next_event_name: "FOMC",
        next_event_importance: "high",
        minutes_to_next_event: 42,
        active_risk_window: false,
        affected_assets: ["BTCUSDT"],
        enrichment_vendors: ["bls", "bea"],
        bls_actual_enriched: true,
        bea_actual_enriched: true,
      },
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
  const aiReviewSection = sections.find((section) => section.key === "ai_review_reason");
  const marketSignalSection = sections.find((section) => section.key === "market_signal_summary");

  assert.deepEqual(
    sections.map((section) => section.key),
    [
      "current_regime",
      "derivatives_orderbook",
      "ai_review_reason",
      "market_signal_summary",
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
      "AI 검토 사유",
      "당시 시장 신호 요약",
      "거시 이벤트 리스크",
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
  assert.equal(eventSection?.items.find((item) => item.label === "BLS 실제값")?.value, "정보 없음");
  assert.equal(eventSection?.items.find((item) => item.label === "BEA 실제값")?.value, "정보 없음");
  assert.equal(aiReviewSection?.items.find((item) => item.label === "검토 분류")?.value, "신규 진입 후보 검토");
  assert.equal(marketSignalSection?.items.find((item) => item.label === "요약")?.value, "모멘텀 강화 / 거래량 strong / 추세 상승 정렬");
});

test("buildOperatorDetailSections keeps legacy AI trigger summary usable as market signal summary", async () => {
  const { buildOperatorDetailSections } = await operatorSymbolDetailModule;
  const baseSymbol = buildSymbol();

  const sections = buildOperatorDetailSections(
    buildSymbol({
      ai_decision: {
        ...baseSymbol.ai_decision,
        ai_review: null,
        ai_review_type: null,
        last_ai_trigger_reason: null,
        ai_trigger_reason_codes: [],
        ai_skip_reason: null,
        last_ai_skip_reason: "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI",
        market_signal_summary: null,
        market_signal_context: null,
        macro_event_risk_summary: null,
        ai_trigger_summary: "legacy momentum/volume/trend feature summary",
      },
    }),
  );

  const aiReviewSection = sections.find((section) => section.key === "ai_review_reason");
  const marketSignalSection = sections.find((section) => section.key === "market_signal_summary");

  assert.equal(marketSignalSection?.title, "당시 시장 신호 요약");
  assert.equal(
    marketSignalSection?.items.find((item) => item.label === "요약")?.value,
    "legacy momentum/volume/trend feature summary",
  );
  assert.equal(
    aiReviewSection?.items.find((item) => item.label === "AI 검토 생략")?.value,
    "거래량 부족으로 AI 검토 생략",
  );
});

test("buildOperatorDetailSections translates new pre-AI skip reasons", async () => {
  const { buildOperatorDetailSections } = await operatorSymbolDetailModule;
  const baseSymbol = buildSymbol();

  const sections = buildOperatorDetailSections(
    buildSymbol({
      ai_decision: {
        ...baseSymbol.ai_decision,
        ai_review: null,
        ai_review_type: null,
        last_ai_trigger_reason: null,
        ai_trigger_reason_codes: [],
        ai_skip_reason: "ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN",
        last_ai_skip_reason: null,
      },
    }),
  );

  const aiReviewSection = sections.find((section) => section.key === "ai_review_reason");
  assert.ok(
    aiReviewSection?.items.some((item) => item.value === "최근 같은 장면의 AI hold 판단 재사용"),
  );
});

test("buildOperatorDetailSections does not reuse generic decision confidence as AI event confidence", async () => {
  const { buildOperatorDetailSections } = await operatorSymbolDetailModule;
  const baseSymbol = buildSymbol();
  const genericPenalty = "Data quality degraded and decision reference freshness blocking";

  const sections = buildOperatorDetailSections(
    buildSymbol({
      event_operator_control: {
        ...baseSymbol.event_operator_control!,
        ai_event_view: {
          ai_bias: "unknown",
          ai_risk_state: "unknown",
          ai_confidence: null,
          scenario_note: null,
          confidence_penalty_reason: null,
          source_state: "unavailable",
        },
      },
      ai_decision: {
        ...baseSymbol.ai_decision,
        confidence: 0.09,
        scenario_note: "Trend continuation candidate with low confidence due to data quality and freshness issues.",
        confidence_penalty_reason: genericPenalty,
        event_risk_acknowledgement: null,
      },
    }),
  );

  const aiEventSection = sections.find((section) => section.key === "ai_event_view");

  assert.ok(aiEventSection);
  assert.notEqual(aiEventSection.items[2]?.value, "0.09");
  assert.notEqual(aiEventSection.items[4]?.value, genericPenalty);
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
      ai_decision: {
        ...buildSymbol().ai_decision,
        macro_event_risk_summary: null,
      },
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

test("buildOperatorDetailSections shows previous complete reference for incomplete FRED snapshot", async () => {
  const { buildOperatorDetailSections } = await operatorSymbolDetailModule;
  const { describeSourceStatus } = await eventOperatorControlModule;
  const baseSymbol = buildSymbol();

  const sections = buildOperatorDetailSections(
    buildSymbol({
      event_operator_control: {
        ...baseSymbol.event_operator_control!,
        event_context: {
          ...baseSymbol.event_operator_control!.event_context,
          source_status: "incomplete",
          source_provenance: "external_api",
          source_vendor: "fred",
          generated_at: "2026-04-20T11:00:00Z",
          is_stale: false,
          is_complete: false,
          active_risk_window: false,
          next_event_at: "2026-04-20T12:30:00Z",
          next_event_name: "Latest Partial CPI",
          next_event_importance: "high",
          minutes_to_next_event: 90,
          failed_release_ids: [50],
          parse_failed_release_ids: [46],
          complete_reference: {
            source_status: "available",
            source_provenance: "external_api",
            source_vendor: "fred",
            generated_at: "2026-04-20T10:00:00Z",
            next_event_at: "2026-04-20T12:30:00Z",
            next_event_name: "Previous Complete CPI",
            next_event_importance: "high",
            minutes_to_next_event: 150,
            active_risk_window: false,
            upcoming_events: [],
            affected_assets: ["BTCUSDT"],
            enrichment_vendors: [],
            summary_note: "previous complete market snapshot",
          },
          upcoming_events: [],
          affected_assets: ["BTCUSDT"],
          summary_note: "최신 조회 일부 실패 / 직전 완전본 참고",
        },
      },
    }),
  );

  const eventSection = sections.find((section) => section.key === "upcoming_event_risk");

  assert.ok(eventSection);
  assert.equal(eventSection.items.find((item) => item.label === "이벤트 요약")?.value, "이벤트 데이터 지연/불완전");
  assert.equal(eventSection.items.find((item) => item.label === "다음 이벤트")?.value, "Latest Partial CPI");
  assert.equal(
    eventSection.items.find((item) => item.label === "데이터 상태")?.value,
    describeSourceStatus("incomplete", { kind: "event_context" }),
  );
  assert.ok(eventSection.items.find((item) => item.label === "AI 판단 당시 이벤트")?.hint.includes("FOMC"));
  assert.ok(
    eventSection.items.find((item) => item.label === "직전 완전본")?.value.includes("Previous Complete CPI"),
  );
  assert.equal(eventSection.items.find((item) => item.label === "누락 release")?.value, "50, 46");
  assert.ok(eventSection.alerts.some((alert) => alert.text.includes("최신 조회 일부 실패 / 직전 완전본 참고")));
});
