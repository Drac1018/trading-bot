export type OperatorDetailTone = "good" | "warn" | "danger" | "neutral";

export type OperatorDetailItem = {
  label: string;
  value: string;
  hint: string;
};

export type OperatorDetailAlert = {
  tone: OperatorDetailTone;
  text: string;
};

export type OperatorDetailSection = {
  key:
    | "current_regime"
    | "derivatives_orderbook"
    | "event_risk_context"
    | "ai_event_rationale"
    | "risk_guard_decision"
    | "blocked_degraded_reason";
  title: string;
  tone: OperatorDetailTone;
  items: OperatorDetailItem[];
  alerts: OperatorDetailAlert[];
};

export type OperatorDetailSymbolLike = {
  market_context_summary: Record<string, unknown>;
  derivatives_summary?: Record<string, unknown>;
  event_context_summary?: Record<string, unknown>;
  ai_decision: {
    decision: string | null;
    confidence: number | null;
    event_risk_acknowledgement?: string | null;
    confidence_penalty_reason?: string | null;
    scenario_note?: string | null;
  };
  risk_guard: {
    allowed: boolean | null;
    decision: string | null;
    operating_state: string | null;
    approved_risk_pct: number | null;
    approved_leverage: number | null;
    blocked_reason_codes: string[];
  };
  execution: {
    order_id: number | null;
    execution_status: string | null;
    order_status: string | null;
  };
  blocked_reasons: string[];
  stale_flags: string[];
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function formatPercent(value: number | null) {
  if (value === null) {
    return "-";
  }
  return `${(value * 100).toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}%`;
}

function formatMaybeNumber(value: number | null, digits = 2) {
  if (value === null) {
    return "-";
  }
  return value.toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function formatMinutes(value: number | null) {
  if (value === null) {
    return "-";
  }
  return `${formatMaybeNumber(value, 0)}분`;
}

function formatDecision(value: string | null | undefined) {
  switch (value) {
    case "hold":
      return "보류";
    case "long":
      return "롱";
    case "short":
      return "숏";
    case "reduce":
      return "축소";
    case "exit":
      return "청산";
    default:
      return value ?? "-";
  }
}

function formatAllowed(value: boolean | null) {
  if (value === null) {
    return "평가 없음";
  }
  return value ? "허용" : "차단";
}

function formatExecutionState(execution: OperatorDetailSymbolLike["execution"], riskAllowed: boolean | null) {
  if (!execution.order_id) {
    return riskAllowed === false ? "실행 없음" : "주문 없음";
  }
  return execution.execution_status ?? execution.order_status ?? "pending";
}

function translateEventImportance(value: string | null) {
  switch (value) {
    case "high":
      return "높음";
    case "medium":
      return "보통";
    case "low":
      return "낮음";
    default:
      return value ?? "-";
  }
}

function translateEventSourceStatus(value: string | null) {
  switch (value) {
    case "fixture":
      return "fixture";
    case "stub":
      return "stub";
    case "unavailable":
      return "소스 없음";
    case "stale":
      return "지연";
    case "incomplete":
      return "불완전";
    default:
      return value ?? "-";
  }
}

function translateEventWindow(active: boolean | null) {
  if (active === null) {
    return "-";
  }
  return active ? "활성" : "비활성";
}

function translateFlag(value: string) {
  const labels: Record<string, string> = {
    account: "계좌 stale",
    positions: "포지션 stale",
    open_orders: "주문 stale",
    protective_orders: "보호주문 stale",
    market_snapshot: "시장 스냅샷 stale",
    market_snapshot_incomplete: "시장 스냅샷 불완전",
    feature_input_missing: "피처 입력 없음",
  };
  return labels[value] ?? value;
}

function unique(values: string[]) {
  return values.filter((item, index, array) => array.indexOf(item) === index);
}

export function buildOperatorDetailSections(symbol: OperatorDetailSymbolLike): OperatorDetailSection[] {
  const regime = asRecord(symbol.market_context_summary);
  const derivatives = asRecord(symbol.derivatives_summary);
  const eventContext = asRecord(symbol.event_context_summary);
  const blockedReasons = unique(
    symbol.risk_guard.blocked_reason_codes.length > 0
      ? symbol.risk_guard.blocked_reason_codes
      : symbol.blocked_reasons,
  );
  const degradedFlags = unique(symbol.stale_flags);
  const eventSourceStatus = asString(eventContext.source_status);
  const eventActiveRiskWindow = asBoolean(eventContext.active_risk_window);
  const nextEventName = asString(eventContext.next_event_name);
  const nextEventTime = asString(eventContext.next_event_at);
  const nextEventImportance = asString(eventContext.next_event_importance);
  const minutesToNextEvent = asNumber(eventContext.minutes_to_next_event);
  const eventBias = asString(eventContext.event_bias);
  const eventRiskAcknowledgement = asString(symbol.ai_decision.event_risk_acknowledgement);
  const confidencePenaltyReason = asString(symbol.ai_decision.confidence_penalty_reason);
  const scenarioNote = asString(symbol.ai_decision.scenario_note);
  const derivativesAvailable = asBoolean(derivatives.available);
  const derivativesSource = asString(derivatives.source);

  const blockedAndDegradedAlerts: OperatorDetailAlert[] = [
    ...blockedReasons.map((code) => ({ tone: "danger" as const, text: `risk 차단: ${code}` })),
    ...degradedFlags.map((flag) => ({ tone: "warn" as const, text: `상태 저하: ${translateFlag(flag)}` })),
  ];
  if (eventSourceStatus && ["unavailable", "stale", "incomplete"].includes(eventSourceStatus)) {
    blockedAndDegradedAlerts.push({
      tone: eventSourceStatus === "unavailable" ? "danger" : "warn",
      text: `이벤트 컨텍스트: ${translateEventSourceStatus(eventSourceStatus)}`,
    });
  }
  if (blockedAndDegradedAlerts.length === 0) {
    blockedAndDegradedAlerts.push({
      tone: "neutral",
      text: "현재 차단/저하 사유 없음",
    });
  }

  return [
    {
      key: "current_regime",
      title: "현재 레짐",
      tone: "neutral",
      items: [
        { label: "주 레짐", value: asString(regime.primary_regime) ?? "-", hint: "현재 시장 상태의 descriptive 요약" },
        { label: "정렬", value: asString(regime.trend_alignment) ?? "-", hint: "방향 정렬 상태" },
        { label: "변동성", value: asString(regime.volatility_regime) ?? "-", hint: "현재 변동성 상태" },
        { label: "거래량", value: asString(regime.volume_regime) ?? "-", hint: "현재 거래량 참여도" },
        { label: "모멘텀", value: asString(regime.momentum_state) ?? "-", hint: "현재 모멘텀 상태" },
      ],
      alerts: [],
    },
    {
      key: "derivatives_orderbook",
      title: "파생 / 오더북 요약",
      tone: derivativesAvailable ? "neutral" : "warn",
      items: [
        {
          label: "가용성",
          value: derivativesAvailable === null ? "-" : derivativesAvailable ? "정상" : "없음",
          hint: `소스 ${derivativesSource ?? "unknown"}`,
        },
        {
          label: "테이커 흐름",
          value: asString(derivatives.taker_flow_alignment) ?? "-",
          hint: "파생 흐름 정렬",
        },
        {
          label: "펀딩 바이어스",
          value: asString(derivatives.funding_bias) ?? "-",
          hint: "현재 펀딩 headwind/tailwind",
        },
        {
          label: "베이시스 바이어스",
          value: asString(derivatives.basis_bias) ?? "-",
          hint: "선물 basis 방향",
        },
        {
          label: "스프레드",
          value: asNumber(derivatives.spread_bps) === null ? "-" : `${formatMaybeNumber(asNumber(derivatives.spread_bps), 2)}bps`,
          hint: asBoolean(derivatives.spread_stress) ? "오더북 스트레스 높음" : "오더북 스트레스 없음",
        },
        {
          label: "군집 리스크",
          value:
            asBoolean(derivatives.crowded_long_risk) || asBoolean(derivatives.crowded_short_risk)
              ? "주의"
              : "정상",
          hint: `long ${String(asBoolean(derivatives.crowded_long_risk) ?? false)} / short ${String(
            asBoolean(derivatives.crowded_short_risk) ?? false,
          )}`,
        },
      ],
      alerts: [],
    },
    {
      key: "event_risk_context",
      title: "이벤트 리스크",
      tone:
        eventActiveRiskWindow === true
          ? "danger"
          : eventSourceStatus && ["unavailable", "stale", "incomplete"].includes(eventSourceStatus)
            ? "warn"
            : "neutral",
      items: [
        {
          label: "다음 이벤트",
          value: nextEventName ?? "-",
          hint: eventBias ? `bias ${eventBias}` : "bias 정보 없음",
        },
        {
          label: "이벤트 시각",
          value: nextEventTime ?? "-",
          hint: "forward-looking event layer",
        },
        {
          label: "남은 시간",
          value: formatMinutes(minutesToNextEvent),
          hint: "다음 이벤트까지 남은 시간",
        },
        {
          label: "중요도",
          value: translateEventImportance(nextEventImportance),
          hint: "운영용 중요도 분류",
        },
        {
          label: "위험 구간",
          value: translateEventWindow(eventActiveRiskWindow),
          hint: "현재 risk window 활성 여부",
        },
        {
          label: "소스 상태",
          value: translateEventSourceStatus(eventSourceStatus),
          hint: `complete ${String(asBoolean(eventContext.is_complete) ?? false)} / stale ${String(
            asBoolean(eventContext.is_stale) ?? false,
          )}`,
        },
      ],
      alerts:
        nextEventName === null && eventSourceStatus === "stub"
          ? [{ tone: "neutral", text: "예정 이벤트 없음" }]
          : [],
    },
    {
      key: "ai_event_rationale",
      title: "AI ?대깽???댁꽍",
      tone: eventRiskAcknowledgement || confidencePenaltyReason || scenarioNote ? "warn" : "neutral",
      items: [
        {
          label: "?대깽???몄떇",
          value: eventRiskAcknowledgement ?? "-",
          hint: "AI媛 ?덉젙?딅맂 ?대깽??由ъ뒪?⑥? ?대뼸寃?留먯꽣瑜?read-only濡??쒖떆",
        },
        {
          label: "??듬룄 ?섎컮 ?ъ쑀",
          value: confidencePenaltyReason ?? "-",
          hint: "confidence瑜??묐┝ ?ъ쑀. 理쒖쥌 ?덉슜/李⑤떒??risk_guard媛 ?곗꽑",
        },
        {
          label: "?쒕굹由ъ삤 硫붾え",
          value: scenarioNote ?? "-",
          hint: "event-aware ?ㅻ챸 硫붾え. ?ㅽ뻾 沅뚰븳怨??섎뱶 寃뚯씠?몄? ?딅떎",
        },
      ],
      alerts:
        eventRiskAcknowledgement || confidencePenaltyReason || scenarioNote
          ? []
          : [{ tone: "neutral", text: "AI event-aware ?ㅻ챸 ?놁쓬" }],
    },
    {
      key: "risk_guard_decision",
      title: "리스크 가드 판정",
      tone:
        symbol.risk_guard.allowed === null
          ? "neutral"
          : symbol.risk_guard.allowed
            ? "good"
            : "danger",
      items: [
        {
          label: "허용 여부",
          value: formatAllowed(symbol.risk_guard.allowed),
          hint: "결정론적 hard gate 결과",
        },
        {
          label: "최종 결정",
          value: formatDecision(symbol.risk_guard.decision),
          hint: `AI 추천 ${formatDecision(symbol.ai_decision.decision)}`,
        },
        {
          label: "운영 상태",
          value: symbol.risk_guard.operating_state ?? "-",
          hint: "현재 risk_guard 기준 operating state",
        },
        {
          label: "승인 risk",
          value: formatPercent(symbol.risk_guard.approved_risk_pct),
          hint: "최종 허용 거래당 risk",
        },
        {
          label: "승인 leverage",
          value:
            symbol.risk_guard.approved_leverage === null
              ? "-"
              : `${formatMaybeNumber(symbol.risk_guard.approved_leverage, 2)}x`,
          hint: "최종 허용 leverage",
        },
        {
          label: "실행 상태",
          value: formatExecutionState(symbol.execution, symbol.risk_guard.allowed),
          hint: "risk 결과와 분리된 실제 주문/체결 상태",
        },
      ],
      alerts: [],
    },
    {
      key: "blocked_degraded_reason",
      title: "차단 / 저하 사유",
      tone: blockedReasons.length > 0 ? "danger" : degradedFlags.length > 0 ? "warn" : "neutral",
      items: [
        {
          label: "risk 차단 코드",
          value: blockedReasons.length > 0 ? blockedReasons.join(", ") : "-",
          hint: "신규 진입 차단 사유 raw code",
        },
        {
          label: "상태 저하 코드",
          value: degradedFlags.length > 0 ? degradedFlags.join(", ") : "-",
          hint: "sync / feature / source degraded 상태",
        },
      ],
      alerts: blockedAndDegradedAlerts,
    },
  ];
}
