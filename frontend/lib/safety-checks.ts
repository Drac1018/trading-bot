export type SafetyCheckRow = Record<string, unknown> & {
  id?: number | string | null;
  audit_event_id?: number | string | null;
  symbol?: string | null;
  decision?: string | null;
  allowed?: boolean | null;
  reason_codes?: unknown;
  blocked_reason_codes?: unknown;
  approved_risk_pct?: number | null;
  approved_leverage?: number | null;
  payload?: unknown;
  risk_guard_result?: unknown;
  pending_entry_plan?: unknown;
  ai_review?: unknown;
  ai_trigger_summary?: unknown;
  market_signal_summary?: unknown;
  market_signal_context?: unknown;
  created_at?: string | null;
};

export type SafetyCheckAuditEventRow = Record<string, unknown> & {
  id?: number | string | null;
  event_type?: string | null;
  entity_id?: number | string | null;
  payload?: unknown;
};

export type SafetyCheckSummaryRow = Record<string, unknown> & {
  id?: number | string | null;
  risk_check_id?: number | string | null;
  audit_event_id?: number | string | null;
  has_audit_event?: boolean | null;
  symbol?: string | null;
  decision?: string | null;
  intent?: string | null;
  allowed?: boolean | null;
  reason_codes?: unknown;
  blocked_reason_codes?: unknown;
  blocked_reason?: string | null;
  created_at?: string | null;
};

export type SafetyCheckDetailPayload = {
  risk_check?: SafetyCheckRow | null;
  audit_event?: SafetyCheckAuditEventRow | null;
};

export type SafetyTone = "good" | "warn" | "danger" | "neutral";

export type SafetyLabel = {
  label: string;
  detail?: string;
  tone?: SafetyTone;
};

export type SafetyReasonDisplay = {
  code: string;
  label: string;
  blocking: boolean;
};

export type SafetyDetailSection = {
  title: string;
  rows: SafetyLabel[];
};

export type SafetyCheckView = {
  id: string;
  auditEventId: string;
  symbol: string;
  createdAtLabel: string;
  requestedAction: string;
  aiSummary: string;
  resultLabel: string;
  resultTone: SafetyTone;
  reasonDisplays: SafetyReasonDisplay[];
  marketDataStatus: SafetyLabel;
  accountTrustStatus: SafetyLabel;
  positionSummary: SafetyLabel;
  totalExposure: SafetyLabel;
  singlePositionRatio: SafetyLabel;
  directionalBias: SafetyLabel;
  tierConcentration: SafetyLabel;
  leverageLimit: SafetyLabel;
  dailyLossStatus: SafetyLabel;
  consecutiveLossStatus: SafetyLabel;
  detailSections: SafetyDetailSection[];
  rawJson: string;
};

export type SafetyCheckSummaryView = {
  id: string;
  auditEventLabel: string;
  hasAuditEvent: boolean;
  symbol: string;
  createdAtLabel: string;
  requestedAction: string;
  intentLabel: string;
  resultLabel: string;
  resultTone: SafetyTone;
  blockedReasonSummary: string;
  reasonDisplays: SafetyReasonDisplay[];
};

const REASON_LABELS: Record<string, string> = {
  ACCOUNT_STATE_INCOMPLETE: "계좌 상태가 불완전함",
  ACCOUNT_STATE_INCONSISTENT: "계좌 상태 불일치",
  ACCOUNT_STATE_STALE: "계좌 상태가 오래됨",
  ACCOUNT_STATE_UNTRUSTED: "계좌 상태 신뢰 불가",
  CHASE_LIMIT_EXCEEDED: "추격 진입 한도 초과",
  DAILY_LOSS_LIMIT_REACHED: "일일 손실 한도 도달",
  DEGRADED_MANAGE_ONLY: "관리 전용 모드",
  DETERMINISTIC_BASELINE_DISAGREEMENT: "AI 판단과 규칙 기반 기준선 불일치",
  DIRECTIONAL_BIAS_LIMIT_REACHED: "방향 편중 한도 초과",
  EMERGENCY_EXIT: "비상 청산 경로",
  EXPECTED_COST_GATE_FAILED: "예상 비용 대비 기대값 부족",
  ALT_ENTRY_LEAD_CONTEXT_UNAVAILABLE: "대체 진입 선행 근거 부족",
  GROSS_EXPOSURE_LIMIT_REACHED: "총 노출도 한도 초과",
  HOLD_DECISION: "AI 판단이 보류임",
  LARGEST_POSITION_LIMIT_REACHED: "단일 포지션 비중 한도 초과",
  LIVE_APPROVAL_REQUIRED: "실거래 승인 필요",
  MARKET_DATA_INCOMPLETE: "시장 데이터가 불완전함",
  MARKET_DATA_STALE: "시장 데이터가 오래됨",
  MARKET_STATE_INCOMPLETE: "시장 데이터가 불완전함",
  MARKET_STATE_STALE: "시장 데이터가 오래됨",
  MAX_CONSECUTIVE_LOSSES_REACHED: "연속 손실 한도 도달",
  OPEN_ORDERS_STATE_STALE: "오픈 주문 상태가 오래됨",
  POSITION_STATE_INCOMPLETE: "포지션 상태가 불완전함",
  POSITION_STATE_STALE: "포지션 상태가 오래됨",
  PROTECTION_STATE_UNVERIFIED: "보호 주문 상태 미확인",
  REDUCE_ONLY_REQUIRED: "reduce_only만 허용",
  SAME_TIER_CONCENTRATION_LIMIT_REACHED: "동일 tier 집중도 한도 초과",
  SLIPPAGE_THRESHOLD_EXCEEDED: "슬리피지 허용 범위 초과",
  TRADING_PAUSED: "거래 일시정지 상태",
};

const ACTION_LABELS: Record<string, string> = {
  buy: "롱 진입",
  close: "청산",
  hold: "보류",
  long: "롱 진입",
  no_trade: "거래 없음",
  reduce: "감소",
  sell: "숏 진입",
  short: "숏 진입",
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function asString(value: unknown): string | null {
  if (typeof value === "string" && value.trim().length > 0) {
    return value;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return null;
}

function asNumber(value: unknown): number | null {
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
  return typeof value === "boolean" ? value : null;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map(asString).filter((item): item is string => item !== null);
}

function getPath(source: unknown, path: string[]): unknown {
  let current: unknown = source;
  for (const key of path) {
    const record = asRecord(current);
    if (!record || !(key in record)) {
      return undefined;
    }
    current = record[key];
  }
  return current;
}

function firstValue(source: unknown, paths: string[][]): unknown {
  for (const path of paths) {
    const value = getPath(source, path);
    if (value !== undefined && value !== null && value !== "") {
      return value;
    }
  }
  return undefined;
}

function mergeCodes(...values: unknown[]): string[] {
  const codes: string[] = [];
  for (const value of values) {
    for (const code of asStringArray(value)) {
      const normalizedCode = code.trim().replace(/[\s-]+/g, "_").toUpperCase();
      if (normalizedCode && !codes.includes(normalizedCode)) {
        codes.push(normalizedCode);
      }
    }
  }
  return codes;
}

function auditEventRiskCheckId(event: SafetyCheckAuditEventRow): string | null {
  const payload = asRecord(event.payload);
  return asString(
    event.event_type === "risk_check"
      ? event.entity_id
      : payload?.risk_check_id ?? payload?.risk_id ?? event.entity_id,
  );
}

export function attachSafetyCheckAuditEventIds(
  rows: SafetyCheckRow[],
  auditEvents: SafetyCheckAuditEventRow[],
): SafetyCheckRow[] {
  const auditEventIdByRiskCheckId = new Map<string, string>();
  for (const event of auditEvents) {
    const riskCheckId = auditEventRiskCheckId(event);
    const auditEventId = asString(event.id);
    if (!riskCheckId || !auditEventId || auditEventIdByRiskCheckId.has(riskCheckId)) {
      continue;
    }
    auditEventIdByRiskCheckId.set(riskCheckId, auditEventId);
  }

  return rows.map((row) => {
    if (row.audit_event_id !== undefined && row.audit_event_id !== null) {
      return row;
    }
    const riskCheckId = asString(row.id);
    const auditEventId = riskCheckId ? auditEventIdByRiskCheckId.get(riskCheckId) : undefined;
    return auditEventId ? { ...row, audit_event_id: auditEventId } : row;
  });
}

function parseApiTimestamp(raw: string): Date {
  const hasTimeZoneSuffix = /(?:z|[+-]\d{2}:?\d{2})$/i.test(raw);
  return new Date(hasTimeZoneSuffix ? raw : `${raw}Z`);
}

const KOREA_TIME_ZONE = "Asia/Seoul";

function formatDateTime(value: unknown): string {
  const raw = asString(value);
  if (!raw) {
    return "시간 없음";
  }
  const date = parseApiTimestamp(raw);
  if (Number.isNaN(date.getTime())) {
    return raw;
  }
  const formatted = new Intl.DateTimeFormat("ko-KR", {
    timeZone: KOREA_TIME_ZONE,
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
  return `${formatted} KST`;
}

function formatNumber(value: unknown, suffix = ""): string {
  const number = asNumber(value);
  if (number === null) {
    return "확인 필요";
  }
  return `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 }).format(number)}${suffix}`;
}

function formatPercent(value: unknown): string {
  const number = asNumber(value);
  if (number === null) {
    return "확인 필요";
  }
  return `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(number)}%`;
}

function formatUsdt(value: unknown): string {
  const number = asNumber(value);
  if (number === null) {
    return "확인 필요";
  }
  return `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(number)} USDT`;
}

function boolStateLabel(value: unknown, trueLabel: string, falseLabel: string): string {
  const bool = asBoolean(value);
  if (bool === null) {
    return "확인 필요";
  }
  return bool ? trueLabel : falseLabel;
}

function reasonLabel(code: string): string {
  const normalizedCode = code.trim().replace(/[\s-]+/g, "_").toUpperCase();
  return REASON_LABELS[normalizedCode] ?? normalizedCode.replaceAll("_", " ").toLowerCase();
}

function actionLabel(raw: string | null): string {
  if (!raw) {
    return "요청 액션 없음";
  }
  return ACTION_LABELS[raw.toLowerCase()] ?? raw;
}

function compactJson(value: unknown): string {
  if (value === undefined || value === null) {
    return "없음";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "원본 JSON 확인 필요";
  }
}

function label(labelText: string, value: unknown, formatter: (value: unknown) => string = compactJson): SafetyLabel {
  return { label: labelText, detail: formatter(value) };
}

function buildReasonDisplays(row: SafetyCheckRow, payload: Record<string, unknown>, riskResult: Record<string, unknown>): SafetyReasonDisplay[] {
  const blockingCodes = mergeCodes(
    row.blocked_reason_codes,
    payload.blocked_reason_codes,
    riskResult.blocked_reason_codes,
  );
  const allCodes = mergeCodes(blockingCodes, row.reason_codes, payload.reason_codes, riskResult.reason_codes);
  return allCodes.map((code) => ({
    code,
    label: reasonLabel(code),
    blocking: blockingCodes.includes(code),
  }));
}

function hasCode(codes: string[], fragments: string[]) {
  return codes.some((code) => fragments.some((fragment) => code.includes(fragment)));
}

function buildResultLabel(row: SafetyCheckRow, payload: Record<string, unknown>, riskResult: Record<string, unknown>, codes: string[]) {
  const decision = asString(row.decision ?? payload.decision ?? riskResult.decision)?.toLowerCase() ?? "";
  const allowed = asBoolean(row.allowed ?? payload.allowed ?? riskResult.allowed);
  const operatingState = asString(payload.operating_state ?? riskResult.operating_state)?.toLowerCase() ?? "";
  const emergency = hasCode(codes, ["EMERGENCY"]) || operatingState.includes("emergency");
  const reduceOnly = Boolean(
    decision.includes("reduce") ||
      decision.includes("close") ||
      asBoolean(payload.reduce_only_allowed) ||
      asBoolean(riskResult.reduce_only_allowed) ||
      hasCode(codes, ["REDUCE_ONLY", "DEGRADED_MANAGE_ONLY"]),
  );

  if (emergency) {
    return { resultLabel: "비상 경로", resultTone: "danger" as const };
  }
  if (reduceOnly && allowed !== false) {
    return { resultLabel: "reduce_only 허용", resultTone: "warn" as const };
  }
  if (allowed === true) {
    return { resultLabel: "승인", resultTone: "good" as const };
  }
  if (allowed === false) {
    return { resultLabel: "차단", resultTone: "danger" as const };
  }
  return { resultLabel: "확인 필요", resultTone: "neutral" as const };
}

function buildMarketStatus(payload: Record<string, unknown>, codes: string[]): SafetyLabel {
  const syncSummary = asRecord(payload.sync_freshness_summary);
  const marketState = asString(
    firstValue(payload, [
      ["market_data_status"],
      ["market_state"],
      ["market_snapshot_status"],
      ["debug_payload", "market_data_status"],
      ["debug_payload", "market_state"],
    ]),
  );
  if (hasCode(codes, ["MARKET_STATE_STALE", "MARKET_DATA_STALE"]) || marketState?.toLowerCase().includes("stale")) {
    return { label: "시장 데이터가 오래됨", detail: marketState ?? "reason code 기준", tone: "danger" };
  }
  if (
    hasCode(codes, ["MARKET_STATE_INCOMPLETE", "MARKET_DATA_INCOMPLETE"]) ||
    marketState?.toLowerCase().includes("incomplete")
  ) {
    return { label: "시장 데이터가 불완전함", detail: marketState ?? "reason code 기준", tone: "warn" };
  }
  const macro = asRecord(payload.macro_event_risk_summary);
  const incomplete = asBoolean(macro?.is_incomplete) ?? asBoolean(macro?.is_complete) === false;
  if (incomplete) {
    return { label: "시장 보조 데이터가 불완전함", detail: compactJson(macro), tone: "warn" };
  }
  if (syncSummary) {
    return { label: "시장 데이터 확인됨", detail: "risk payload 기준", tone: "good" };
  }
  return { label: "확인 필요", detail: "market snapshot 요약을 원본 JSON에서 확인", tone: "neutral" };
}

function buildAccountTrustStatus(payload: Record<string, unknown>, codes: string[]): SafetyLabel {
  const accountFreshness = asRecord(getPath(payload, ["sync_freshness_summary", "account"]));
  const accountStatus = asString(accountFreshness?.status ?? accountFreshness?.raw_status);
  if (hasCode(codes, ["ACCOUNT_STATE_UNTRUSTED", "ACCOUNT_STATE_INCONSISTENT"])) {
    return { label: "계좌 상태 신뢰 불가", detail: accountStatus ?? "reason code 기준", tone: "danger" };
  }
  if (hasCode(codes, ["ACCOUNT_STATE_STALE"]) || asBoolean(accountFreshness?.stale) === true) {
    return { label: "계좌 상태가 오래됨", detail: accountStatus ?? "freshness 기준", tone: "warn" };
  }
  if (hasCode(codes, ["ACCOUNT_STATE_INCOMPLETE"]) || asBoolean(accountFreshness?.incomplete) === true) {
    return { label: "계좌 상태가 불완전함", detail: accountStatus ?? "freshness 기준", tone: "warn" };
  }
  if (accountStatus) {
    return { label: "계좌 상태 신뢰 가능", detail: accountStatus, tone: "good" };
  }
  return { label: "확인 필요", detail: "account snapshot 요약을 원본 JSON에서 확인", tone: "neutral" };
}

function buildPositionSummary(payload: Record<string, unknown>): SafetyLabel {
  const metrics = asRecord(payload.exposure_metrics);
  const openCount = asNumber(metrics?.open_position_count);
  const longExposure = metrics?.long_exposure_pct_equity;
  const shortExposure = metrics?.short_exposure_pct_equity;
  if (openCount !== null) {
    return {
      label: `${openCount}개 포지션`,
      detail: `롱 ${formatPercent(longExposure)} / 숏 ${formatPercent(shortExposure)}`,
      tone: "neutral",
    };
  }
  const positionSummary = firstValue(payload, [
    ["position_summary"],
    ["positions_snapshot"],
    ["positions"],
    ["debug_payload", "position_summary"],
  ]);
  if (positionSummary !== undefined) {
    return { label: "포지션 요약 있음", detail: compactJson(positionSummary), tone: "neutral" };
  }
  return { label: "포지션 정보 없음", detail: "원본 JSON 확인", tone: "neutral" };
}

function drawdownStateLabel(value: unknown): string {
  const raw = asString(value);
  if (!raw) {
    return "확인 필요";
  }
  const normalized = raw.toLowerCase();
  if (normalized === "normal") {
    return "정상";
  }
  if (normalized.includes("recover")) {
    return "회복 중";
  }
  if (normalized.includes("warn") || normalized.includes("caution")) {
    return "주의";
  }
  if (normalized.includes("degrad") || normalized.includes("reduce") || normalized.includes("restricted")) {
    return "축소 운용";
  }
  if (normalized.includes("halt") || normalized.includes("stop") || normalized.includes("blocked")) {
    return "차단";
  }
  return raw;
}

function drawdownStateTone(value: unknown): SafetyTone {
  const normalized = asString(value)?.toLowerCase() ?? "";
  if (normalized === "normal" || normalized.includes("recover")) {
    return "good";
  }
  if (normalized.includes("halt") || normalized.includes("stop") || normalized.includes("blocked")) {
    return "danger";
  }
  if (normalized.includes("warn") || normalized.includes("caution") || normalized.includes("degrad") || normalized.includes("reduce")) {
    return "warn";
  }
  return "neutral";
}

function formatRatioPercent(value: unknown): string {
  const number = asNumber(value);
  if (number === null) {
    return "확인 필요";
  }
  const percent = Math.abs(number) <= 1 ? number * 100 : number;
  return formatPercent(percent);
}

function policyAdjustmentSummary(value: unknown): string | null {
  const policy = asRecord(value);
  if (!policy) {
    return null;
  }
  const riskMultiplier = asNumber(policy.risk_pct_multiplier);
  const leverageMultiplier = asNumber(policy.leverage_multiplier);
  const notionalMultiplier = asNumber(policy.notional_multiplier);
  const capacityMultiplier = asNumber(policy.entry_capacity_multiplier);
  const thresholdUplift = asNumber(policy.entry_score_threshold_uplift);
  const winnerOnly = asBoolean(policy.winner_only_pyramiding);
  const adjusted =
    (riskMultiplier !== null && riskMultiplier !== 1) ||
    (leverageMultiplier !== null && leverageMultiplier !== 1) ||
    (notionalMultiplier !== null && notionalMultiplier !== 1) ||
    (capacityMultiplier !== null && capacityMultiplier !== 1) ||
    (thresholdUplift !== null && thresholdUplift !== 0) ||
    winnerOnly === true;
  if (!adjusted) {
    return "정책 조정 없음";
  }
  const parts: string[] = [];
  if (riskMultiplier !== null && riskMultiplier !== 1) {
    parts.push(`risk ${formatNumber(riskMultiplier, "x")}`);
  }
  if (leverageMultiplier !== null && leverageMultiplier !== 1) {
    parts.push(`leverage ${formatNumber(leverageMultiplier, "x")}`);
  }
  if (notionalMultiplier !== null && notionalMultiplier !== 1) {
    parts.push(`notional ${formatNumber(notionalMultiplier, "x")}`);
  }
  if (capacityMultiplier !== null && capacityMultiplier !== 1) {
    parts.push(`capacity ${formatNumber(capacityMultiplier, "x")}`);
  }
  if (thresholdUplift !== null && thresholdUplift !== 0) {
    parts.push(`score +${formatNumber(thresholdUplift)}`);
  }
  if (winnerOnly === true) {
    parts.push("winner only");
  }
  return parts.join(" / ");
}

function drawdownSummary(value: unknown): { label: string; detail: string; tone: SafetyTone } | null {
  const state = asRecord(value);
  if (!state) {
    return null;
  }
  const currentState = state.current_drawdown_state ?? state.state ?? state.drawdown_state;
  const parts = [
    `낙폭 ${formatPercent(state.drawdown_depth_pct)}`,
    `최근 순손익 ${formatUsdt(state.recent_net_pnl)}`,
    `연속 손실 ${formatNumber(state.consecutive_losses, "회")}`,
  ];
  const policySummary = policyAdjustmentSummary(state.policy_adjustments);
  if (policySummary) {
    parts.push(policySummary);
  }
  const latestSnapshot = asString(state.latest_pnl_snapshot_at);
  if (latestSnapshot) {
    parts.push(`스냅샷 ${formatDateTime(latestSnapshot)}`);
  }
  const recoveryProgress = asNumber(state.recovery_progress);
  if (recoveryProgress !== null && recoveryProgress < 1) {
    parts.push(`회복 ${formatRatioPercent(recoveryProgress)}`);
  }
  return {
    label: drawdownStateLabel(currentState),
    detail: parts.join(" / "),
    tone: drawdownStateTone(currentState),
  };
}

function buildDailyLossStatus(payload: Record<string, unknown>, codes: string[]): SafetyLabel {
  if (hasCode(codes, ["DAILY_LOSS_LIMIT"])) {
    return { label: "일일 손실 한도 도달", detail: "reason code 기준", tone: "danger" };
  }
  const value = firstValue(payload, [
    ["daily_loss_state"],
    ["drawdown_state"],
    ["debug_payload", "daily_loss_state"],
    ["debug_payload", "drawdown_state"],
  ]);
  if (value !== undefined) {
    const summary = drawdownSummary(value);
    if (summary) {
      return summary;
    }
    return { label: "일일 손실 상태", detail: compactJson(value), tone: "neutral" };
  }
  return { label: "위반 없음", detail: "차단 reason code 없음", tone: "good" };
}

function buildConsecutiveLossStatus(payload: Record<string, unknown>, codes: string[]): SafetyLabel {
  if (hasCode(codes, ["CONSECUTIVE_LOSS"])) {
    return { label: "연속 손실 한도 도달", detail: "reason code 기준", tone: "danger" };
  }
  const losses = firstValue(payload, [
    ["consecutive_losses"],
    ["debug_payload", "consecutive_losses"],
    ["loss_streak"],
    ["debug_payload", "loss_streak"],
    ["daily_loss_state", "consecutive_losses"],
    ["drawdown_state", "consecutive_losses"],
    ["debug_payload", "daily_loss_state", "consecutive_losses"],
    ["debug_payload", "drawdown_state", "consecutive_losses"],
  ]);
  if (losses !== undefined) {
    return { label: "연속 손실 상태", detail: formatNumber(losses, "회"), tone: "neutral" };
  }
  return { label: "위반 없음", detail: "차단 reason code 없음", tone: "good" };
}

function buildDetailSections(row: SafetyCheckRow, payload: Record<string, unknown>, riskResult: Record<string, unknown>): SafetyDetailSection[] {
  const debugPayload = asRecord(payload.debug_payload);
  const exposureMetrics = asRecord(payload.exposure_metrics);
  const syncFreshness = asRecord(payload.sync_freshness_summary);
  const pendingPlan = asRecord(row.pending_entry_plan);

  return [
    {
      title: "시장 스냅샷 요약",
      rows: [
        label("시장 스냅샷 ID", payload.snapshot_id ?? row.market_snapshot_id),
        label("시장 신호", row.market_signal_summary ?? row.ai_trigger_summary),
        label("시장 신호 맥락", row.market_signal_context),
        label("거시 이벤트 리스크", row.macro_event_risk_summary ?? payload.macro_event_risk_summary),
        label("파생상품 맥락", debugPayload?.market_derivatives_context),
      ],
    },
    {
      title: "계정 스냅샷 요약",
      rows: [
        label("계정 최신성", syncFreshness?.account),
        label("미체결 주문 최신성", syncFreshness?.open_orders),
        label("보호 주문 최신성", syncFreshness?.protective_orders),
        label("거래소 레버리지", debugPayload?.exchange_leverage),
        label("정합성 확인 상태", debugPayload?.reconciliation_state),
      ],
    },
    {
      title: "포지션 스냅샷 요약",
      rows: [
        label("포지션 최신성", syncFreshness?.positions),
        label("열린 포지션 수", exposureMetrics?.open_position_count, formatNumber),
        label("롱 노출", exposureMetrics?.long_exposure_pct_equity, formatPercent),
        label("숏 노출", exposureMetrics?.short_exposure_pct_equity, formatPercent),
        label("판단 심볼 주문가치", exposureMetrics?.decision_symbol_notional, formatUsdt),
      ],
    },
    {
      title: "리스크 입력 요약",
      rows: [
        label("AI 판단", payload.decision ?? row.decision),
        label("심볼", row.symbol ?? payload.symbol),
        label("요청 주문가치", debugPayload?.requested_notional ?? payload.raw_projected_notional, formatUsdt),
        label("요청 수량", debugPayload?.requested_quantity, formatNumber),
        label("예상 비용 점검", debugPayload?.expected_cost_gate),
        label("대기 진입 계획", pendingPlan),
      ],
    },
    {
      title: "리스크 결과 요약",
      rows: [
        label("승인 여부", riskResult.allowed ?? payload.allowed ?? row.allowed, (value) =>
          boolStateLabel(value, "승인", "차단"),
        ),
        label("승인 리스크 비율", riskResult.approved_risk_pct ?? payload.approved_risk_pct, formatPercent),
        label("승인 레버리지", riskResult.approved_leverage ?? payload.approved_leverage, formatNumber),
        label("승인 주문가치", payload.approved_notional ?? payload.approved_projected_notional, formatUsdt),
        label("사유 코드", riskResult.reason_codes ?? payload.reason_codes),
        label("차단 사유 코드", riskResult.blocked_reason_codes ?? payload.blocked_reason_codes),
      ],
    },
  ];
}

export function buildSafetyCheckView(row: SafetyCheckRow): SafetyCheckView {
  const payload = asRecord(row.payload) ?? {};
  const riskResult = asRecord(row.risk_guard_result) ?? {};
  const debugPayload = asRecord(payload.debug_payload) ?? {};
  const exposureMetrics = asRecord(payload.exposure_metrics) ?? {};
  const reasonDisplays = buildReasonDisplays(row, payload, riskResult);
  const codes = reasonDisplays.map((reason) => reason.code);
  const { resultLabel, resultTone } = buildResultLabel(row, payload, riskResult, codes);
  const action = asString(payload.decision ?? riskResult.decision ?? row.decision);
  const riskCheckId = asString(row.id) ?? "unknown";
  const auditEventId = asString(payload.audit_event_id ?? row.audit_event_id) ?? "없음";

  return {
    id: riskCheckId,
    auditEventId,
    symbol: asString(row.symbol ?? payload.symbol) ?? "심볼 없음",
    createdAtLabel: formatDateTime(row.created_at),
    requestedAction: actionLabel(action),
    aiSummary: asString(row.ai_trigger_summary ?? row.market_signal_summary) ?? "AI 요약 없음",
    resultLabel,
    resultTone,
    reasonDisplays,
    marketDataStatus: buildMarketStatus(payload, codes),
    accountTrustStatus: buildAccountTrustStatus(payload, codes),
    positionSummary: buildPositionSummary(payload),
    totalExposure: {
      label: "총 노출도",
      detail: formatPercent(exposureMetrics.gross_exposure_pct_equity ?? exposureMetrics.total_exposure_pct_equity),
    },
    singlePositionRatio: {
      label: "단일 포지션 비중",
      detail: formatPercent(exposureMetrics.largest_position_pct_equity ?? exposureMetrics.decision_symbol_concentration_pct),
    },
    directionalBias: {
      label: "방향 편중",
      detail: formatPercent(exposureMetrics.directional_bias_pct),
    },
    tierConcentration: {
      label: "tier 집중도",
      detail: formatPercent(exposureMetrics.same_tier_concentration_pct),
    },
    leverageLimit: {
      label: "레버리지 제한",
      detail: formatNumber(payload.effective_leverage_cap ?? row.approved_leverage ?? payload.approved_leverage, "x"),
    },
    dailyLossStatus: buildDailyLossStatus(payload, codes),
    consecutiveLossStatus: buildConsecutiveLossStatus(payload, codes),
    detailSections: buildDetailSections(row, payload, riskResult),
    rawJson: JSON.stringify(row, null, 2),
  };
}

export function buildSafetyCheckSummaryView(row: SafetyCheckSummaryRow): SafetyCheckSummaryView {
  const payload = asRecord(row.payload) ?? {};
  const riskResult = asRecord(row.risk_guard_result) ?? {};
  const reasonDisplays = buildReasonDisplays(row, payload, riskResult);
  const codes = reasonDisplays.map((reason) => reason.code);
  const { resultLabel, resultTone } = buildResultLabel(row, payload, riskResult, codes);
  const action = asString(row.decision ?? payload.decision ?? riskResult.decision);
  const intent = asString(row.intent ?? payload.intent ?? payload.intent_family ?? payload.management_action);
  const riskCheckId = asString(row.risk_check_id ?? row.id) ?? "unknown";
  const auditEventId = asString(row.audit_event_id);
  const hasAuditEvent = asBoolean(row.has_audit_event) ?? auditEventId !== null;
  const blockingReasons = reasonDisplays.filter((reason) => reason.blocking);
  const blockedReason = asString(row.blocked_reason ?? payload.blocked_reason ?? riskResult.blocked_reason);
  const blockedReasonSummary =
    blockingReasons.length > 0
      ? blockingReasons.map((reason) => reason.label).join(" / ")
      : blockedReason
        ? reasonLabel(blockedReason)
        : row.allowed === true
          ? "차단 없음"
          : "차단 사유 확인 필요";

  return {
    id: riskCheckId,
    auditEventLabel: auditEventId ? `감사 이벤트 #${auditEventId}` : hasAuditEvent ? "감사 이벤트 있음" : "감사 이벤트 없음",
    hasAuditEvent,
    symbol: asString(row.symbol ?? payload.symbol) ?? "심볼 없음",
    createdAtLabel: formatDateTime(row.created_at),
    requestedAction: actionLabel(action),
    intentLabel: intent ? actionLabel(intent) : "의도 없음",
    resultLabel,
    resultTone,
    blockedReasonSummary,
    reasonDisplays,
  };
}
