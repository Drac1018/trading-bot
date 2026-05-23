function normalizeCode(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  if (!trimmed) {
    return null;
  }
  return trimmed.replace(/[\s-]+/g, "_").toUpperCase();
}

function rawValues(values: readonly string[] | null | undefined): string[] {
  return (values ?? []).map((value) => value.trim()).filter((value) => value.length > 0);
}

const marketDataSourceLabels: Record<string, string> = {
  BINANCE: "Binance Futures 실시간/최근 캔들 데이터",
  BINANCE_FUTURES: "Binance Futures 실시간/최근 캔들 데이터",
  BINANCE_FUTURES_MARKET_STREAM: "Binance Futures 실시간/최근 캔들 데이터",
  BINANCE_MARKET_STREAM: "Binance Futures 실시간/최근 캔들 데이터",
  BINANCE_WS: "Binance Futures 실시간/최근 캔들 데이터",
  BINANCE_WS_FINAL_KLINE: "Binance Futures 실시간 완성 캔들 데이터",
  BINANCE_REST: "Binance Futures 최근 캔들 데이터",
  REST: "Binance Futures 최근 캔들 데이터",
  REDIS: "공유 캐시의 최근 시장 데이터",
  SHARED_CACHE: "공유 캐시의 최근 시장 데이터",
  CACHE: "공유 캐시의 최근 시장 데이터",
  MEMORY: "프로세스 메모리 캐시",
  LOCAL_MEMORY: "프로세스 메모리 캐시",
  PROCESS_MEMORY: "프로세스 메모리 캐시",
  SEED_FALLBACK: "시드 보조 데이터",
  SNAPSHOT: "저장된 시장 스냅샷",
};

const marketDataStatusLabels: Record<string, string> = {
  FRESH: "정상",
  OK: "정상",
  HEALTHY: "정상",
  AVAILABLE: "정상",
  CONNECTED: "정상",
  RUNNING: "정상",
  ACTIVE: "정상",
  EXTERNAL_API: "정상",
  REST_FALLBACK: "REST 보조 경로 사용",
  STALE: "오래됨",
  MARKET_STREAM_STALE: "오래됨",
  DEGRADED: "주의 필요",
  INCOMPLETE: "불완전",
  PARTIAL: "불완전",
  UNAVAILABLE: "사용할 수 없음",
  DISCONNECTED: "사용할 수 없음",
  FAILED: "사용할 수 없음",
  ERROR: "사용할 수 없음",
  INACTIVE: "비활성",
  DISABLED: "비활성",
  OPTIONAL: "선택 사용",
  REQUIRED: "필수 사용",
  MISSING: "확인 필요",
  UNKNOWN: "확인 필요",
};

const marketDecisionLabels: Record<string, string> = {
  ENTER_LONG: "롱 진입 제안",
  LONG: "롱 진입 제안",
  BUY: "매수",
  ENTER_SHORT: "숏 진입 제안",
  SHORT: "숏 진입 제안",
  SELL: "매도",
  HOLD: "관망",
  REDUCE: "포지션 축소",
  EXIT: "청산",
  CLOSE: "청산",
};

const marketContextValueLabels: Record<string, string> = {
  RANGE: "횡보",
  BULLISH: "상승",
  BEARISH: "하락",
  TRANSITION: "전환",
  MIXED: "혼재",
  BULLISH_ALIGNED: "상승 정렬",
  BEARISH_ALIGNED: "하락 정렬",
  RANGE_BOUND: "횡보",
  WEAK: "약함",
  NORMAL: "보통",
  MEDIUM: "보통",
  STRONG: "강함",
  LOW: "낮음",
  HIGH: "높음",
  STRENGTHENING: "강화 중",
  STABLE: "안정",
  WEAKENING: "약화 중",
  COMPRESSED: "압축",
  EXPANDING: "확장",
  EXPANDED: "확장",
  PROCESS: "프로세스 단위",
  LOCAL: "로컬",
  GLOBAL: "공유",
  FRESH: "정상",
  STALE: "오래됨",
  INCOMPLETE: "불완전",
  UNAVAILABLE: "사용할 수 없음",
  UNKNOWN: "확인 필요",
};

const marketReasonCodeLabels: Record<string, string> = {
  STALE_MARKET_DATA: "시장 데이터가 오래되어 신규 진입을 막았습니다",
  MARKET_DATA_STALE: "시장 데이터가 오래되어 신규 진입을 막았습니다",
  MARKET_STATE_STALE: "시장 데이터가 오래되어 신규 진입을 막았습니다",
  MARKET_STREAM_STALE: "실시간 시장 데이터가 오래되어 신규 진입을 막았습니다",
  MARKET_STREAM_CACHE_UNAVAILABLE: "실시간 시장 데이터 캐시를 사용할 수 없어 REST 보조 경로를 사용 중입니다",
  MARKET_STREAM_MISSING_OR_STALE: "실시간 시장 데이터가 없거나 오래되어 REST 보조 경로를 사용 중입니다",
  MARKET_STREAM_CACHE_METADATA_MISMATCH: "실시간 시장 데이터 캐시 메타데이터가 맞지 않아 REST 보조 경로를 사용 중입니다",
  MARKET_STREAM_CACHE_STALE: "실시간 시장 데이터 캐시가 오래되어 REST 보조 경로를 사용 중입니다",
  MARKET_STREAM_CACHE_CORRUPT: "실시간 시장 데이터 캐시를 읽을 수 없어 REST 보조 경로를 사용 중입니다",
  MARKET_STREAM_OLDER_THAN_REST: "실시간 스트림보다 REST 최신 캔들이 더 최신이라 REST 보조 경로를 사용 중입니다",
  MARKET_STREAM_CONNECTION_DROPPED: "실시간 시장 데이터 스트림 연결이 끊겼습니다",
  NO_MARKET_STREAMS_CONFIGURED: "실시간 시장 데이터 스트림 설정이 없습니다",
  MARKET_STREAM_EVENT_INVALID: "실시간 시장 데이터 이벤트 형식이 올바르지 않습니다",
  PARTIAL_KLINE_IGNORED: "완성 전 캔들은 시장 스냅샷에 반영하지 않습니다",
  KLINE_CLOSED: "완성 캔들이 시장 스냅샷에 반영됐습니다",
  MARKET_SNAPSHOT_INCOMPLETE: "시장 스냅샷이 불완전합니다",
  MARKET_SNAPSHOT_MISSING: "시장 스냅샷이 아직 없습니다",
  REST_FAILED_STREAM_ONLY_INCOMPLETE: "REST 보조 조회가 실패했고 실시간 스트림 데이터도 불완전합니다",
  INCOMPLETE_MARKET_DATA: "시장 데이터가 불완전하여 신규 진입을 막았습니다",
  MARKET_DATA_INCOMPLETE: "시장 데이터가 불완전하여 신규 진입을 막았습니다",
  MARKET_STATE_INCOMPLETE: "시장 데이터가 불완전하여 신규 진입을 막았습니다",
  MARKET_DATA_UNAVAILABLE: "시장 데이터를 사용할 수 없어 신규 진입을 막았습니다",
  BINANCE_REST_CIRCUIT_OPEN: "Binance REST 경로가 불안정해 신규 진입을 막았습니다",
  ACCOUNT_STATE_STALE: "계좌 정보가 오래되어 신규 진입을 막았습니다",
  POSITION_STATE_STALE: "포지션 정보가 오래되어 신규 진입을 막았습니다",
  POSITIONS_STATE_STALE: "포지션 정보가 오래되어 신규 진입을 막았습니다",
  OPEN_ORDERS_STATE_STALE: "열린 주문 정보가 오래되어 신규 진입을 막았습니다",
  PROTECTIVE_ORDERS_STATE_STALE: "보호 주문 정보가 오래되어 신규 진입을 막았습니다",
  PROTECTION_STATE_UNVERIFIED: "보호 주문 상태가 확인되지 않아 신규 진입을 막았습니다",
  MISSING_PROTECTIVE_ORDERS: "필수 보호 주문이 없어 신규 진입을 막았습니다",
  PROTECTIVE_ORDER_FAILURE: "보호 주문 점검에 실패해 신규 진입을 막았습니다",
  UNRESOLVED_SUBMISSION_GUARD_ACTIVE: "이전 주문 제출 결과가 불명확해 신규 진입을 막았습니다",
  LIVE_ORDER_SUBMISSION_UNKNOWN: "주문 제출 결과가 불명확해 신규 진입을 막았습니다",
  TRADING_PAUSED: "운영 중지 상태라 신규 진입을 막았습니다",
  MANUAL_USER_REQUEST: "운영자가 수동으로 신규 진입을 막았습니다",
  LIVE_APPROVAL_REQUIRED: "실거래 승인 창이 닫혀 신규 진입을 막았습니다",
  LIVE_APPROVAL_POLICY_DISABLED: "실거래 승인 정책이 꺼져 신규 진입을 막았습니다",
  LIVE_TRADING_DISABLED: "실거래 실행 설정이 꺼져 신규 진입을 막았습니다",
  LIVE_ENV_DISABLED: "실거래 환경 플래그가 꺼져 신규 진입을 막았습니다",
  LIVE_TRADING_ENV_DISABLED: "실거래 환경 플래그가 꺼져 신규 진입을 막았습니다",
  ROLLOUT_MODE_SHADOW: "관찰 모드라 실제 신규 진입을 막았습니다",
  ROLLOUT_MODE_LIVE_DRY_RUN: "실거래 사전 검증 모드라 실제 신규 진입을 막았습니다",
  HOLD_DECISION: "AI가 신규 진입 신호가 없다고 판단했습니다",
  ROLE_DAILY_TOKEN_BUDGET_EXHAUSTED: "trading_decision 일일 AI 토큰 예산이 소진되어 deterministic 판단을 사용했습니다",
  SOFT_SIGNAL_AI_REVIEW: "약한 후보라 AI 검토 대상으로 분류됐습니다",
  DERIVATIVES_ALIGNMENT_HEADWIND: "파생시장 정합성이 진입 방향을 뒷받침하지 않습니다",
  BREAKOUT_OI_SPREAD_FILTER: "돌파처럼 보여도 OI와 스프레드 조건이 부족합니다",
  BREAKOUT_OI_NOT_EXPANDING: "돌파 확인에 필요한 OI 증가가 없습니다",
  ENTRY_TRIGGER_NOT_MET: "진입 조건이 아직 충족되지 않았습니다",
  CHASE_LIMIT_EXCEEDED: "가격이 이미 지나가 추격 진입을 막았습니다",
  PLAN_MAX_CHASE_EXCEEDED: "대기 중인 진입 계획의 추격 허용 범위를 넘었습니다",
  PLAN_LATE_CHASE_WAITING_REENTRY: "가격이 늦게 따라붙어 더 좋은 재진입 조건을 기다립니다",
  PLAN_CONFIRM_QUALITY_LOW: "진입 확인 신호 품질이 부족합니다",
  DETERMINISTIC_BASELINE_DISAGREEMENT: "AI 판단과 기준선 판단이 달라 즉시 주문을 보류했습니다",
  NO_EDGE: "현재 조건에서 거래 우위가 부족합니다",
  RANGE_CHOP: "횡보 구간 흔들림이 커 신규 진입을 보류했습니다",
  WEAK_VOLUME: "거래량 근거가 약해 신규 진입을 보류했습니다",
  MOMENTUM_WEAKENING: "진입 방향 모멘텀이 약해 신규 진입을 보류했습니다",
  LOW_EDGE_HOLD_CANDIDATE: "거래 우위가 약해 신규 진입을 보류했습니다",
  LOW_CONVICTION_SLOT_EXCLUDED: "확신도가 낮아 신규 진입 후보에서 제외했습니다",
  SCORE_BELOW_THRESHOLD: "진입 점수가 기준보다 낮습니다",
  EXPECTANCY_BELOW_THRESHOLD: "기대값이 기준보다 낮습니다",
  SLIPPAGE_THRESHOLD_EXCEEDED: "허용 가격 차이를 넘어 신규 진입을 막았습니다",
  ADVERSE_SIGNED_SLIPPAGE: "체결 품질이 불리해 신규 진입을 막았습니다",
  EXPOSURE_LIMIT: "노출 한도 때문에 신규 진입을 막았습니다",
  EXPOSURE_LIMIT_EXCEEDED: "노출 한도를 초과해 신규 진입을 막았습니다",
  GROSS_EXPOSURE_LIMIT_REACHED: "총 노출 한도에 도달해 신규 진입을 막았습니다",
  GROSS_EXPOSURE_LIMIT_EXCEEDED: "총 노출 한도를 초과해 신규 진입을 막았습니다",
  ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT: "총 노출 한도에 맞춰 진입 크기를 줄였습니다",
  ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT: "방향 편중 한도에 맞춰 진입 크기를 줄였습니다",
  ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT: "단일 포지션 한도에 맞춰 진입 크기를 줄였습니다",
  ENTRY_CLAMPED_TO_SAME_TIER_LIMIT: "동일 티어 집중도 한도에 맞춰 진입 크기를 줄였습니다",
  DIRECTIONAL_LIMIT_EXCEEDED: "방향 편중 한도를 초과해 신규 진입을 막았습니다",
  SINGLE_POSITION_LIMIT_EXCEEDED: "단일 포지션 한도를 초과해 신규 진입을 막았습니다",
  LARGEST_POSITION_LIMIT_REACHED: "단일 포지션 한도에 도달해 신규 진입을 막았습니다",
  SAME_TIER_CONCENTRATION_LIMIT_REACHED: "동일 티어 집중도 한도에 도달해 신규 진입을 막았습니다",
  CORRELATION_RISK_LIMIT_REACHED: "상관 위험 한도에 도달해 신규 진입을 막았습니다",
  DUPLICATE_EXPOSURE: "중복 노출을 막기 위해 신규 진입을 보류했습니다",
  INSUFFICIENT_MARGIN: "가용 증거금이 부족해 주문을 실행하지 못했습니다",
  ENTRY_SIZE_BELOW_MIN_NOTIONAL: "진입 금액이 최소 주문 금액보다 작습니다",
  INVALID_INVALIDATION_PRICE: "무효화 가격 기준이 맞지 않아 신규 진입을 막았습니다",
};

export function marketDataSourceLabel(source: string | null | undefined): string {
  const key = normalizeCode(source);
  if (!key) {
    return "시장 데이터 소스 확인 필요";
  }
  return marketDataSourceLabels[key] ?? "시장 데이터 소스 확인 필요";
}

export function marketDataStatusLabel(status: string | null | undefined): string {
  const key = normalizeCode(status);
  if (!key) {
    return "확인 필요";
  }
  return marketDataStatusLabels[key] ?? "확인 필요";
}

export function marketDecisionLabel(value: string | null | undefined): string {
  const key = normalizeCode(value);
  if (!key) {
    return "-";
  }
  return marketDecisionLabels[key] ?? "알 수 없는 판단";
}

export function marketContextValueLabel(value: string | null | undefined): string {
  const key = normalizeCode(value);
  if (!key) {
    return "-";
  }
  return marketContextValueLabels[key] ?? "확인 필요";
}

export function marketReasonCodeLabel(value: string | null | undefined): string {
  const key = normalizeCode(value);
  if (!key) {
    return "차단 사유 없음";
  }
  return marketReasonCodeLabels[key] ?? "알 수 없는 차단 사유";
}

export function formatMarketReasonCodeLabels(values: readonly string[] | null | undefined): string {
  const codes = rawValues(values);
  if (codes.length === 0) {
    return "-";
  }
  return codes.map((code) => marketReasonCodeLabel(code)).join(", ");
}

export function formatMarketReasonCodeDebugLabels(values: readonly string[] | null | undefined): string {
  const codes = rawValues(values);
  if (codes.length === 0) {
    return "-";
  }
  return codes.map((code) => `${marketReasonCodeLabel(code)} (원본: ${code})`).join(", ");
}

export function formatMarketRawReasonCodes(values: readonly string[] | null | undefined): string | null {
  const codes = rawValues(values);
  if (codes.length === 0) {
    return null;
  }
  return `원본 코드: ${codes.join(", ")}`;
}
