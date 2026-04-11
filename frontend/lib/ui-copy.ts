const labelMap: Record<string, string> = {
  id: "ID",
  title: "제목",
  summary: "요약",
  detail: "상세 내용",
  problem: "문제",
  proposal: "제안 내용",
  rationale: "근거",
  status: "상태",
  severity: "심각도",
  priority: "우선순위",
  effort: "작업량",
  impact: "영향도",
  source: "출처",
  source_type: "출처 유형",
  symbol: "심볼",
  timeframe: "타임프레임",
  mode: "운영 모드",
  decision: "의사결정",
  confidence: "신뢰도",
  latest_price: "현재가",
  latest_volume: "최신 거래량",
  stop_loss: "손절가",
  take_profit: "익절가",
  leverage: "레버리지",
  approved_leverage: "승인 레버리지",
  risk_pct: "리스크 비중",
  approved_risk_pct: "승인 리스크 비중",
  rationale_codes: "근거 코드",
  reason_codes: "차단 사유",
  provider_name: "AI 공급자",
  role: "에이전트 역할",
  workflow: "워크플로",
  schedule_window: "실행 주기",
  next_run_at: "다음 실행 시각",
  started_at: "시작 시각",
  completed_at: "완료 시각",
  created_at: "생성 시각",
  updated_at: "수정 시각",
  snapshot_time: "스냅샷 시각",
  feature_time: "피처 시각",
  opened_at: "포지션 시작",
  closed_at: "포지션 종료",
  applied_at: "적용 시각",
  verification_summary: "검증 내용",
  files_changed: "변경 파일",
  linked_backlog_id: "연결 backlog ID",
  related_backlog_id: "연결 backlog ID",
  linked_backlog_title: "연결 backlog 제목",
  related_backlog_title: "연결 backlog 제목",
  order_type: "주문 유형",
  side: "주문 방향",
  requested_quantity: "요청 수량",
  requested_price: "요청 가격",
  filled_quantity: "누적 체결 수량",
  average_fill_price: "평균 체결가",
  fill_price: "체결가",
  fill_quantity: "체결 수량",
  fee_paid: "수수료",
  slippage_pct: "슬리피지",
  event_type: "이벤트 유형",
  entity_type: "대상 유형",
  entity_id: "대상 ID",
  message: "메시지",
  payload: "세부 Payload",
  live_trading_enabled: "실거래 사용",
  live_execution_ready: "실행 준비",
  trading_paused: "거래 일시중지",
  open_positions: "오픈 포지션 수",
  daily_pnl: "일일 손익",
  cumulative_pnl: "누적 손익",
  blocked_reasons: "차단 사유",
  manual_live_approval: "수동 승인 게이트",
  live_execution_armed_until: "실거래 승인 만료 시각",
  ai_enabled: "AI 사용",
  ai_model: "AI 모델",
  ai_call_interval_minutes: "AI 최소 호출 간격(분)",
  decision_cycle_interval_minutes: "의사결정 주기(분)",
  tracked_symbols: "추적 심볼",
  default_symbol: "기본 심볼",
  default_timeframe: "기본 타임프레임",
  binance_market_data_enabled: "Binance 시세 사용",
  binance_api_key_configured: "Binance Key 설정",
  binance_api_secret_configured: "Binance Secret 설정",
  openai_api_key_configured: "OpenAI Key 설정",
  market_snapshot_id: "시장 스냅샷 ID",
  decision_run_id: "의사결정 실행 ID",
};

const valueMap: Record<string, string> = {
  hold: "보류",
  long: "롱",
  short: "숏",
  reduce: "축소",
  exit: "청산",
  paused: "거래 중지",
  live: "실거래",
  live_ready: "실행 가능",
  live_guarded: "가드 모드",
  market_data_only: "시장 데이터만 수집",
  ai_active: "AI 활성",
  pending: "대기",
  partially_filled: "부분 체결",
  filled: "체결",
  cancelled: "취소",
  rejected: "거부",
  open: "오픈",
  closed: "종료",
  completed: "완료",
  success: "성공",
  failed: "실패",
  skipped: "건너뜀",
  running: "실행 중",
  manual: "수동",
  low: "낮음",
  medium: "보통",
  high: "높음",
  critical: "긴급",
  small: "작음",
  large: "큼",
  monitor: "모니터링",
  act: "실행 권고",
  ok: "정상",
  info: "정보",
  warning: "경고",
  error: "오류",
  ai: "AI",
  user: "사용자",
  manual_source: "수동",
  "deterministic-mock": "결정론적 Mock",
  openai: "OpenAI",
  binance: "Binance",
};

const reasonCodeMap: Record<string, string> = {
  TRADING_PAUSED: "거래가 일시중지 상태여서 실행이 차단됐습니다.",
  STALE_MARKET_DATA: "시장 데이터가 오래되어 신규 진입이 차단됐습니다.",
  INCOMPLETE_MARKET_DATA: "시장 데이터가 불완전하여 실행이 차단됐습니다.",
  DAILY_LOSS_LIMIT_REACHED: "일일 손실 한도에 도달해 추가 진입이 차단됐습니다.",
  MAX_CONSECUTIVE_LOSSES_REACHED: "연속 손실 한도 도달로 HOLD가 우선됩니다.",
  LEVERAGE_EXCEEDS_LIMIT: "레버리지가 허용 한도를 초과했습니다.",
  RISK_PCT_EXCEEDS_LIMIT: "거래당 리스크 비중이 허용 한도를 초과했습니다.",
  MISSING_STOP_OR_TARGET: "손절가 또는 익절가가 없어 진입이 차단됐습니다.",
  INVALID_LONG_BRACKETS: "롱 포지션의 손절/익절 구조가 유효하지 않습니다.",
  INVALID_SHORT_BRACKETS: "숏 포지션의 손절/익절 구조가 유효하지 않습니다.",
  SLIPPAGE_THRESHOLD_EXCEEDED: "슬리피지가 허용 범위를 초과했습니다.",
  HOLD_DECISION: "현재 판단이 HOLD라 신규 진입이 차단됐습니다.",
  LIVE_ENV_DISABLED: "실거래 환경 플래그가 비활성화되어 있습니다.",
  AI_DISABLED: "AI가 꺼져 있어 배치 실행이 건너뛰어졌습니다.",
};

const percentKeys = new Set([
  "confidence",
  "risk_pct",
  "approved_risk_pct",
  "volatility_pct",
  "drawdown_pct",
  "max_risk_per_trade",
  "max_daily_loss",
  "slippage_threshold_pct",
]);

const priceKeys = new Set([
  "latest_price",
  "entry_zone_min",
  "entry_zone_max",
  "stop_loss",
  "take_profit",
  "requested_price",
  "fill_price",
  "average_fill_price",
  "daily_pnl",
  "cumulative_pnl",
  "realized_pnl",
  "unrealized_pnl",
  "fee_paid",
  "entry_price",
  "mark_price",
  "liquidation_price",
  "notional",
  "wallet_balance",
  "margin_balance",
  "available_balance",
]);

const leverageKeys = new Set(["leverage", "approved_leverage", "max_leverage"]);
const datetimeKeys = new Set([
  "created_at",
  "updated_at",
  "started_at",
  "completed_at",
  "snapshot_time",
  "feature_time",
  "next_run_at",
  "live_execution_armed_until",
  "opened_at",
  "closed_at",
  "applied_at",
]);

const isoDatePattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/;

function humanizeKey(key: string) {
  return key.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function translateString(value: string) {
  return reasonCodeMap[value] ?? valueMap[value] ?? value;
}

function formatDateTime(value: string) {
  const parsed = new Date(value.endsWith("Z") ? value : `${value}Z`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}

function formatBoolean(key: string | undefined, value: boolean) {
  if (key === "allowed") {
    return value ? "허용" : "차단";
  }
  if (key === "schema_valid") {
    return value ? "정상" : "실패";
  }
  if (key === "trading_paused") {
    return value ? "중지됨" : "가동 중";
  }
  if (key?.endsWith("_configured")) {
    return value ? "설정됨" : "미설정";
  }
  if (key?.endsWith("_enabled")) {
    return value ? "활성화" : "비활성화";
  }
  if (key?.startsWith("is_")) {
    return value ? "예" : "아니오";
  }
  return value ? "예" : "아니오";
}

function formatNumber(key: string | undefined, value: number) {
  if (percentKeys.has(key ?? "")) {
    return `${(value * 100).toLocaleString("ko-KR", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    })}%`;
  }

  if (leverageKeys.has(key ?? "")) {
    return `${value.toLocaleString("ko-KR", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    })}x`;
  }

  if (priceKeys.has(key ?? "")) {
    return value.toLocaleString("ko-KR", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    });
  }

  return value.toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: Number.isInteger(value) ? 0 : 4,
  });
}

export function translateLabel(key: string) {
  return labelMap[key] ?? humanizeKey(key);
}

export function formatDisplayValue(value: unknown, key?: string): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "boolean") {
    return formatBoolean(key, value);
  }
  if (typeof value === "number") {
    return formatNumber(key, value);
  }
  if (typeof value === "string") {
    if ((key && datetimeKeys.has(key)) || isoDatePattern.test(value)) {
      return formatDateTime(value);
    }
    return translateString(value);
  }
  return String(value);
}

export function normalizeDisplayValue(value: unknown, key?: string): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => normalizeDisplayValue(item, key));
  }

  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([childKey, childValue]) => [
        translateLabel(childKey),
        normalizeDisplayValue(childValue, childKey),
      ]),
    );
  }

  return formatDisplayValue(value, key);
}

export function getRowTitle(row: Record<string, unknown>, index: number) {
  const symbol = typeof row.symbol === "string" ? row.symbol : null;
  const timeframe = typeof row.timeframe === "string" ? row.timeframe : null;
  if (symbol && timeframe) {
    return `${symbol} / ${timeframe}`;
  }

  if (typeof row.title === "string") {
    return row.title;
  }

  if (typeof row.role === "string") {
    return `${translateString(row.role)} 실행`;
  }

  if (typeof row.workflow === "string") {
    return translateString(row.workflow);
  }

  if (typeof row.event_type === "string") {
    return translateString(row.event_type);
  }

  return `항목 ${index + 1}`;
}

export function formatListValue(value: unknown, key?: string): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => formatDisplayValue(item, key));
}
