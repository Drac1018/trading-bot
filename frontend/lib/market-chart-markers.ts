export type MarketChartMarkerRow = Record<string, unknown>;

export type MarketChartEventMarkerKind = "ai" | "risk_approved" | "risk_blocked" | "execution";

export type MarketChartEventMarker = {
  timestamp: string;
  kind: MarketChartEventMarkerKind;
  label: string;
  detail: string;
  symbol: string;
  action: string;
  price: number | null;
  statusLabel: string;
  reasonLabel: string | null;
  reasonCodes?: string[];
  sourceId: string | null;
};

export type MarketChartMarkerRows = {
  decisions: MarketChartMarkerRow[];
  riskChecks: MarketChartMarkerRow[];
  orders: MarketChartMarkerRow[];
  executions: MarketChartMarkerRow[];
};

type MarkerCandle = {
  timestamp: string;
};

const commonReasonLabels: Record<string, string> = {
  HOLD_DECISION: "AI가 신규 진입 신호가 없다고 판단했습니다",
  ROLE_DAILY_TOKEN_BUDGET_EXHAUSTED: "trading_decision 일일 AI 토큰 예산이 소진되어 deterministic 판단을 사용했습니다",
  SOFT_SIGNAL_AI_REVIEW: "약한 후보라 AI 검토 대상으로 분류됐습니다",
  DERIVATIVES_ALIGNMENT_HEADWIND: "파생시장 정합성이 진입 방향을 뒷받침하지 않습니다",
  BREAKOUT_OI_SPREAD_FILTER: "돌파처럼 보여도 OI와 스프레드 조건이 부족합니다",
  BREAKOUT_OI_NOT_EXPANDING: "돌파 확인에 필요한 OI 증가가 없습니다",
  ENTRY_TRIGGER_NOT_MET: "진입 조건이 아직 충족되지 않았습니다",
  SLIPPAGE_THRESHOLD_EXCEEDED: "허용 가격 차이를 넘어 신규 진입을 막았습니다",
  CHASE_LIMIT_EXCEEDED: "가격이 이미 지나가 추격 진입을 막았습니다",
  PLAN_MAX_CHASE_EXCEEDED: "대기 중인 진입 계획의 추격 허용 범위를 넘었습니다",
  PLAN_LATE_CHASE_WAITING_REENTRY: "가격이 늦게 따라붙어 더 좋은 재진입 조건을 기다립니다",
  PLAN_CONFIRM_QUALITY_LOW: "진입 확인 신호 품질이 부족합니다",
  DETERMINISTIC_BASELINE_DISAGREEMENT: "AI 판단과 기준선 판단이 달라 즉시 주문을 보류했습니다",
  LIVE_APPROVAL_REQUIRED: "실거래 승인 창이 닫혀 신규 진입을 막았습니다",
  LIVE_TRADING_DISABLED: "실거래 실행 설정이 꺼져 신규 진입을 막았습니다",
  STALE_MARKET_DATA: "시장 데이터가 오래되어 신규 진입을 막았습니다",
  MARKET_DATA_STALE: "시장 데이터가 오래되어 신규 진입을 막았습니다",
  MARKET_STATE_STALE: "시장 데이터가 오래되어 신규 진입을 막았습니다",
  INCOMPLETE_MARKET_DATA: "시장 데이터가 불완전하여 신규 진입을 막았습니다",
  MARKET_DATA_INCOMPLETE: "시장 데이터가 불완전하여 신규 진입을 막았습니다",
  MARKET_STATE_INCOMPLETE: "시장 데이터가 불완전하여 신규 진입을 막았습니다",
  EXPOSURE_LIMIT_EXCEEDED: "노출 한도를 초과해 신규 진입을 막았습니다",
  GROSS_EXPOSURE_LIMIT_REACHED: "총 노출 한도에 도달해 신규 진입을 막았습니다",
  POSITION_STATE_STALE: "포지션 정보가 오래되어 신규 진입을 막았습니다",
  PROTECTION_STATE_UNVERIFIED: "보호 주문 상태가 확인되지 않아 신규 진입을 막았습니다",
  MISSING_PROTECTIVE_ORDERS: "필수 보호 주문이 없어 신규 진입을 막았습니다",
  INSUFFICIENT_MARGIN: "가용 증거금이 부족해 주문을 실행하지 못했습니다",
  POSITION_CLOSED_PROTECTIVE_ORDER_ORPHANED: "종료된 포지션의 보호 주문을 정리했습니다",
};

const internalLabelMap: Record<string, string> = {
  enter_long: "롱 진입 제안",
  long: "롱 진입 제안",
  enter_short: "숏 진입 제안",
  short: "숏 진입 제안",
  hold: "관망",
  reduce: "포지션 축소",
  exit: "청산",
  buy: "매수",
  sell: "매도",
  filled: "체결",
  canceled: "취소",
  cancelled: "취소",
  pending: "대기",
  submitted: "제출",
  rejected: "거절",
  failed: "실패",
  limit: "지정가",
  market: "시장가",
  stop_market: "스탑 시장가",
  take_profit_market: "익절 시장가",
  entry_candidate_review: "진입 후보 AI 검토",
  realtime_cycle: "실시간 주기",
};

export function emptyMarketChartMarkerRows(): MarketChartMarkerRows {
  return {
    decisions: [],
    riskChecks: [],
    orders: [],
    executions: [],
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function asFiniteNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asBoolean(value: unknown): boolean | null {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (normalized === "true") {
      return true;
    }
    if (normalized === "false") {
      return false;
    }
  }
  return null;
}

function asNonEmptyString(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function normalizeSymbol(value: unknown): string | null {
  return asNonEmptyString(value)?.toUpperCase() ?? null;
}

function getPath(row: unknown, path: readonly string[]): unknown {
  let current: unknown = row;
  for (const key of path) {
    const record = asRecord(current);
    if (!record || !(key in record)) {
      return undefined;
    }
    current = record[key];
  }
  return current;
}

function firstString(row: unknown, paths: readonly (readonly string[])[]): string | null {
  for (const path of paths) {
    const value = asNonEmptyString(getPath(row, path));
    if (value) {
      return value;
    }
  }
  return null;
}

function firstDateTime(row: unknown, paths: readonly (readonly string[])[]): string | null {
  for (const path of paths) {
    const raw = getPath(row, path);
    const text = asNonEmptyString(raw);
    if (text) {
      return text;
    }
    const numeric = asFiniteNumber(raw);
    if (numeric !== null) {
      const millis = numeric > 1_000_000_000_000 ? numeric : numeric * 1000;
      const parsed = new Date(millis);
      if (!Number.isNaN(parsed.getTime())) {
        return parsed.toISOString();
      }
    }
  }
  return null;
}

function firstPrice(row: unknown, paths: readonly (readonly string[])[]): number | null {
  for (const path of paths) {
    const value = asFiniteNumber(getPath(row, path));
    if (value !== null && value > 0) {
      return value;
    }
  }
  return null;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => asNonEmptyString(item)).filter((item): item is string => item !== null);
}

function firstStringList(row: unknown, paths: readonly (readonly string[])[]): string[] {
  for (const path of paths) {
    const values = stringList(getPath(row, path));
    if (values.length > 0) {
      return values;
    }
  }
  return [];
}

function formatInternalLabel(value: string | null | undefined): string {
  if (!value) {
    return "-";
  }
  const normalized = value.trim();
  const lower = normalized.toLowerCase();
  if (internalLabelMap[lower]) {
    return internalLabelMap[lower];
  }
  const upper = normalized.toUpperCase();
  if (commonReasonLabels[normalized] ?? commonReasonLabels[upper]) {
    return commonReasonLabels[normalized] ?? commonReasonLabels[upper];
  }
  return "확인 필요";
}

function formatConfidence(value: number | null): string | null {
  return value === null ? null : `신뢰도 ${(value * 100).toFixed(0)}%`;
}

function formatReasonLabel(codes: string[]): string | null {
  if (codes.length === 0) {
    return null;
  }
  return codes
    .map((code) => {
      const label = commonReasonLabels[code] ?? commonReasonLabels[code.toUpperCase()];
      return label ? `${label} (원본: ${code})` : `알 수 없는 차단 사유 (원본: ${code})`;
    })
    .join(", ");
}

function isTradeMarkerDecision(value: string | null): boolean {
  return value === "long" || value === "short" || value === "enter_long" || value === "enter_short" || value === "reduce" || value === "exit";
}

function rowSymbol(row: MarketChartMarkerRow): string | null {
  return (
    normalizeSymbol(row.symbol) ??
    normalizeSymbol(getPath(row, ["output_payload", "symbol"])) ??
    normalizeSymbol(getPath(row, ["input_payload", "ai_trigger", "symbol"])) ??
    normalizeSymbol(getPath(row, ["payload", "symbol"])) ??
    normalizeSymbol(getPath(row, ["payload", "trade", "symbol"]))
  );
}

function matchesSymbol(row: MarketChartMarkerRow, symbol: string): boolean {
  return rowSymbol(row) === symbol.toUpperCase();
}

function sourceId(prefix: string, row: MarketChartMarkerRow): string | null {
  const id = firstString(row, [["id"], [`${prefix}_id`]]) ?? asFiniteNumber(row.id)?.toString() ?? null;
  return id ? `${prefix}:${id}` : null;
}

function riskPrice(row: MarketChartMarkerRow): number | null {
  const direct = firstPrice(row, [
    ["payload", "reference_price"],
    ["payload", "latest_price"],
    ["payload", "mark_price"],
    ["payload", "entry_price"],
    ["payload", "debug_payload", "reference_price"],
    ["payload", "debug_payload", "entry_trigger", "reference_price"],
    ["payload", "debug_payload", "entry_trigger", "latest_price"],
  ]);
  if (direct !== null) {
    return direct;
  }
  const zoneMin = asFiniteNumber(getPath(row, ["pending_entry_plan", "entry_zone_min"]));
  const zoneMax = asFiniteNumber(getPath(row, ["pending_entry_plan", "entry_zone_max"]));
  if (zoneMin !== null && zoneMax !== null && zoneMin > 0 && zoneMax > 0) {
    return (zoneMin + zoneMax) / 2;
  }
  return zoneMin !== null && zoneMin > 0 ? zoneMin : null;
}

function buildDecisionMarkers(symbol: string, rows: MarketChartMarkerRow[]): MarketChartEventMarker[] {
  return rows
    .filter((row) => matchesSymbol(row, symbol))
    .flatMap((row): MarketChartEventMarker[] => {
      const timestamp = firstDateTime(row, [
        ["metadata_json", "last_ai_invoked_at"],
        ["completed_at"],
        ["created_at"],
        ["input_payload", "ai_trigger", "triggered_at"],
      ]);
      if (!timestamp) {
        return [];
      }
      const decision = firstString(row, [["decision"], ["output_payload", "decision"]]);
      if (!isTradeMarkerDecision(decision)) {
        return [];
      }
      const confidence = asFiniteNumber(row.confidence) ?? asFiniteNumber(getPath(row, ["output_payload", "confidence"]));
      const confidenceText = formatConfidence(confidence);
      const action = formatInternalLabel(decision);
      return [
        {
          timestamp,
          kind: "ai",
          label: "AI",
          detail: [action, confidenceText].filter(Boolean).join(" / "),
          symbol: symbol.toUpperCase(),
          action,
          price: firstPrice(row, [
            ["output_payload", "entry_price"],
            ["output_payload", "recommended_entry_price"],
            ["output_payload", "reference_price"],
            ["output_payload", "latest_price"],
            ["input_payload", "ai_trigger", "reference_price"],
            ["input_payload", "ai_trigger", "latest_price"],
          ]),
          statusLabel: "AI 추천",
          reasonLabel: null,
          sourceId: sourceId("ai", row),
        },
      ];
    });
}

function buildRiskMarkers(symbol: string, rows: MarketChartMarkerRow[]): MarketChartEventMarker[] {
  return rows
    .filter((row) => matchesSymbol(row, symbol))
    .flatMap((row): MarketChartEventMarker[] => {
      const timestamp = firstDateTime(row, [["created_at"], ["payload", "as_of"]]);
      if (!timestamp) {
        return [];
      }
      const allowed =
        asBoolean(row.allowed) ??
        asBoolean(getPath(row, ["payload", "allowed"])) ??
        asBoolean(getPath(row, ["risk_guard_result", "allowed"]));
      const decision = firstString(row, [["decision"], ["payload", "decision"], ["risk_guard_result", "decision"]]);
      const action = formatInternalLabel(decision);
      const reasonCodes = firstStringList(row, [
        ["blocked_reason_codes"],
        ["payload", "blocked_reason_codes"],
        ["risk_guard_result", "blocked_reason_codes"],
        ["reason_codes"],
        ["payload", "reason_codes"],
      ]);
      const blocked = allowed !== true;
      if (!isTradeMarkerDecision(decision) && reasonCodes.every((code) => code === "HOLD_DECISION")) {
        return [];
      }
      const reasonLabel = blocked ? formatReasonLabel(reasonCodes) : null;
      return [
        {
          timestamp,
          kind: blocked ? "risk_blocked" : "risk_approved",
          label: blocked ? "차단" : "승인",
          detail: blocked ? `${action} 리스크 차단` : `${action} 리스크 승인`,
          symbol: symbol.toUpperCase(),
          action,
          price: riskPrice(row),
          statusLabel: blocked ? "리스크 차단" : "리스크 승인",
          reasonLabel,
          sourceId: sourceId("risk", row),
        },
      ];
    });
}

function buildExecutionMarkers(symbol: string, rows: MarketChartMarkerRow[]): MarketChartEventMarker[] {
  return rows
    .filter((row) => matchesSymbol(row, symbol))
    .flatMap((row): MarketChartEventMarker[] => {
      const timestamp = firstDateTime(row, [["created_at"], ["payload", "trade_time"], ["payload", "trade", "time"]]);
      const price = firstPrice(row, [
        ["fill_price"],
        ["average_fill_price"],
        ["requested_price"],
        ["payload", "trade", "price"],
        ["payload", "requested_price"],
      ]);
      if (!timestamp || price === null) {
        return [];
      }
      const side = firstString(row, [["payload", "side"], ["payload", "trade", "side"]]);
      const orderType = firstString(row, [["order_type"], ["payload", "order_type"]]);
      const status = firstString(row, [["status"], ["order_status"]]);
      const action = [formatInternalLabel(side), formatInternalLabel(orderType)].filter((item) => item !== "-").join(" ");
      return [
        {
          timestamp,
          kind: "execution",
          label: "체결",
          detail: `체결 ${formatInternalLabel(status)}`,
          symbol: symbol.toUpperCase(),
          action: action || "체결",
          price,
          statusLabel: "실제 실행",
          reasonLabel: null,
          sourceId: sourceId("execution", row),
        },
      ];
    });
}

function buildOrderMarkers(symbol: string, rows: MarketChartMarkerRow[], executionOrderIds: Set<number>): MarketChartEventMarker[] {
  return rows
    .filter((row) => matchesSymbol(row, symbol))
    .flatMap((row): MarketChartEventMarker[] => {
      const orderId = asFiniteNumber(row.id);
      const status = firstString(row, [["status"], ["exchange_status"]]);
      if (orderId !== null && executionOrderIds.has(orderId) && status?.toLowerCase() === "filled") {
        return [];
      }
      const timestamp = firstDateTime(row, [["created_at"], ["last_exchange_update_at"]]);
      const price = firstPrice(row, [
        ["average_fill_price"],
        ["requested_price"],
        ["metadata_json", "exchange_order", "actualPrice"],
        ["metadata_json", "exchange_order", "stopPrice"],
        ["metadata_json", "submit_request", "reference_price"],
      ]);
      if (!timestamp || price === null) {
        return [];
      }
      const side = firstString(row, [["side"], ["metadata_json", "exchange_order", "side"]]);
      const orderType = firstString(row, [["order_type"], ["metadata_json", "exchange_order", "orderType"]]);
      const reasonCodes = firstStringList(row, [["reason_codes"]]);
      const action = [formatInternalLabel(side), formatInternalLabel(orderType)].filter((item) => item !== "-").join(" ");
      return [
        {
          timestamp,
          kind: "execution",
          label: status?.toLowerCase() === "filled" ? "체결" : "주문",
          detail: `주문 ${formatInternalLabel(status)}`,
          symbol: symbol.toUpperCase(),
          action: action || "주문",
          price,
          statusLabel: "실제 실행",
          reasonLabel: formatReasonLabel(reasonCodes),
          sourceId: sourceId("order", row),
        },
      ];
    });
}

export function dedupeMarketChartEventMarkers(markers: MarketChartEventMarker[]): MarketChartEventMarker[] {
  const seen = new Set<string>();
  return markers
    .filter((marker) => {
      const key =
        marker.sourceId ??
        `${marker.kind}:${marker.symbol}:${marker.timestamp}:${marker.action}:${marker.price ?? "-"}:${marker.statusLabel}`;
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    })
    .sort((left, right) => {
      const leftTime = timestampMs(left.timestamp) ?? 0;
      const rightTime = timestampMs(right.timestamp) ?? 0;
      return leftTime - rightTime;
    });
}

export function buildMarketChartEventMarkersFromRows(symbol: string, rows: MarketChartMarkerRows): MarketChartEventMarker[] {
  const executionOrderIds = new Set(
    rows.executions
      .map((row) => asFiniteNumber(row.order_id))
      .filter((value): value is number => value !== null),
  );
  return dedupeMarketChartEventMarkers([
    ...buildDecisionMarkers(symbol, rows.decisions),
    ...buildRiskMarkers(symbol, rows.riskChecks),
    ...buildExecutionMarkers(symbol, rows.executions),
    ...buildOrderMarkers(symbol, rows.orders, executionOrderIds),
  ]);
}

export function timestampMs(value: string | null | undefined): number | null {
  if (!value) {
    return null;
  }
  const parsed = Date.parse(value.endsWith("Z") ? value : `${value}Z`);
  return Number.isNaN(parsed) ? null : parsed;
}

export function countVisibleMarketChartMarkers(events: MarketChartEventMarker[], candles: MarkerCandle[]): number {
  if (events.length === 0 || candles.length === 0) {
    return 0;
  }
  const candleTimes = candles.map((item) => timestampMs(item.timestamp)).filter((item): item is number => item !== null);
  if (candleTimes.length === 0) {
    return 0;
  }
  const chartStart = candleTimes[0] ?? 0;
  const chartEnd = candleTimes[candleTimes.length - 1] ?? 0;
  return events.filter((marker) => {
    const markerTime = timestampMs(marker.timestamp);
    return markerTime !== null && markerTime >= chartStart && markerTime <= chartEnd;
  }).length;
}
