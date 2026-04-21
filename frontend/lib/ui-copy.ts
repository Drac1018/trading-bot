import { lookupRiskReasonCode } from "./risk-reason-copy.js";

const labelMap: Record<string, string> = {
  id: "ID",
  title: "제목",
  summary: "요약",
  detail: "상세 내용",
  problem: "문제",
  proposal: "제안 내용",
  rationale: "근거",
  status: "상태",
  event_category: "감사 분류",
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
  latest_volume: "최근 거래량",
  stop_loss: "손절가",
  take_profit: "익절가",
  leverage: "레버리지",
  approved_leverage: "승인 레버리지",
  risk_pct: "리스크 비중",
  approved_risk_pct: "승인 리스크 비중",
  rationale_codes: "근거 코드",
  reason_codes: "판정 사유",
  blocked_reason_codes: "차단 사유",
  adjustment_reason_codes: "조정 사유",
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
  feature_time: "지표 계산 시각",
  opened_at: "오픈 시각",
  closed_at: "종료 시각",
  applied_at: "적용 시각",
  order_type: "주문 유형",
  side: "주문 방향",
  requested_quantity: "요청 수량",
  requested_price: "요청 가격",
  filled_quantity: "체결 수량",
  average_fill_price: "평균 체결가",
  fill_price: "체결가",
  fill_quantity: "체결 수량",
  fee_paid: "수수료",
  slippage_pct: "슬리피지",
  live_trading_enabled: "실거래 사용",
  rollout_mode: "실거래 적용 단계",
  exchange_submit_allowed: "거래소 주문 전송 허용",
  limited_live_max_notional: "제한된 실거래 최대 주문 금액",
  live_execution_ready: "실거래 제출 준비 상태",
  trading_paused: "운영 일시 중지",
  guard_mode_reason_category: "차단 사유 분류",
  guard_mode_reason_code: "차단 사유 코드",
  guard_mode_reason_message: "차단 사유 설명",
  exchange_can_trade: "거래소 주문 가능 상태",
  app_live_armed: "앱 실거래 준비",
  approval_window_open: "실거래 승인 창",
  paused: "운영 일시 중지",
  degraded: "안전 모드",
  risk_allowed: "신규 진입 허용 여부",
  blocked_reasons_current_cycle: "이번 판단 주기 차단 사유",
  control_status_summary: "제어 상태 요약",
  app_live_execution_ready: "앱 실주문 준비 상태",
  app_trading_paused: "앱 거래 중지",
  app_operating_state: "앱 운영 상태",
  app_pause_reason_code: "앱 중지 사유",
  app_pause_origin: "앱 중지 주체",
  app_auto_resume_last_blockers: "앱 자동 복구 차단 사유",
  open_positions: "보유 포지션",
  daily_pnl: "일일 손익",
  cumulative_pnl: "누적 손익",
  blocked_reasons: "차단 사유",
  latest_blocked_reasons: "최근 신규 진입 차단 사유",
  manual_live_approval: "수동 실거래 승인",
  live_execution_armed_until: "실거래 승인 만료 시각",
  ai_enabled: "AI 사용",
  ai_model: "AI 모델",
  ai_call_interval_minutes: "AI 기본 검토 간격(분)",
  decision_cycle_interval_minutes: "재검토 확인 주기(분)",
  tracked_symbols: "추적 심볼",
  default_symbol: "기본 심볼",
  default_timeframe: "기본 시장 타임프레임",
  exchange_sync_interval_seconds: "거래소 동기화 주기(초)",
  market_refresh_interval_minutes: "시장 갱신 주기(분)",
  position_management_interval_seconds: "포지션 관리 주기(초)",
  symbol_cadence_overrides: "심볼별 주기 설정",
  symbol_effective_cadences: "심볼별 실제 적용 주기",
  timeframe_override: "타임프레임 개별 설정",
  market_refresh_interval_minutes_override: "시장 갱신 개별 설정",
  position_management_interval_seconds_override: "포지션 관리 개별 설정",
  decision_cycle_interval_minutes_override: "재검토 확인 주기 개별 설정",
  ai_call_interval_minutes_override: "AI 기본 검토 간격 개별 설정",
  uses_global_defaults: "전역값 사용 여부",
  last_market_refresh_at: "마지막 시장 갱신",
  last_position_management_at: "마지막 포지션 관리",
  last_decision_at: "마지막 재검토 확인",
  last_ai_decision_at: "마지막 AI 호출",
  next_market_refresh_due_at: "다음 시장 갱신 예정",
  next_position_management_due_at: "다음 포지션 관리 예정",
  next_decision_due_at: "다음 재검토 확인 예정",
  next_ai_call_due_at: "다음 AI 검토 기준 시각",
  binance_market_data_enabled: "Binance 시세 사용",
  binance_api_key_configured: "Binance Key 설정",
  binance_api_secret_configured: "Binance Secret 설정",
  openai_api_key_configured: "OpenAI Key 설정",
  market_snapshot_id: "시장 스냅샷 ID",
  decision_run_id: "의사결정 실행 ID",
  pause_reason_code: "중지 사유",
  pause_origin: "중지 발생 주체",
  pause_reason_detail: "중지 상세",
  pause_triggered_at: "중지 발생 시각",
  auto_resume_after: "자동 복구 예정 시각",
  auto_resume_whitelisted: "자동 복구 정책 대상",
  auto_resume_eligible: "자동 복구 가능",
  auto_resume_status: "자동 복구 상태",
  auto_resume_last_blockers: "자동 복구 차단 사유",
  pause_severity: "중지 심각도",
  pause_recovery_class: "복구 분류",
  operating_state: "운영 상태",
  protection_recovery_status: "보호 복구 상태",
  protection_recovery_active: "보호 복구 진행 여부",
  protection_recovery_failure_count: "보호 복구 실패 누적",
  missing_protection_symbols: "누락 보호 심볼",
  missing_protection_items: "누락 보호 항목",
  protected_positions: "보호된 포지션",
  unprotected_positions: "미보호 포지션",
  position_protection_summary: "포지션 보호 상태",
  protected: "보호 여부",
  protective_order_count: "보호 주문 수",
  has_stop_loss: "손절 주문 존재",
  has_take_profit: "익절 주문 존재",
  missing_components: "누락 보호 항목",
  position_size: "포지션 수량",
  symbol_protection_state: "심볼별 보호 상태",
  emergency_actions_taken: "비상 조치",
  auto_resume_precheck: "자동 복구 사전 점검",
  auto_resume_postcheck: "자동 복구 사후 점검",
  approval_state: "승인 상태",
  approval_detail: "승인 상세",
  blocker_details: "차단 상세",
  symbol_blockers: "심볼별 차단 사유",
  market_data_status: "시장 데이터 상태",
  sync_status: "동기화 상태",
  evaluated_symbols: "평가 대상 심볼",
  protective_orders: "보호 주문 상태",
  trigger_source: "실행 경로",
  pnl_summary: "손익 요약",
  account_sync_summary: "계좌 동기화 요약",
  exposure_summary: "노출 요약",
  execution_policy_summary: "주문 반영 요약",
  market_context_summary: "시장 컨텍스트 요약",
  adaptive_protection_summary: "적응형 보호 요약",
  adaptive_signal_summary: "적응형 신호 요약",
  basis: "기준",
  basis_note: "기준 설명",
  net_realized_pnl: "순실현 손익",
  account_sync_status: "계좌 동기화 상태",
  reconciliation_mode: "보수적 반영 방식",
  account_reconciliation_mode: "보수적 반영 방식",
  freshness_seconds: "마지막 동기화 후 지난 시간",
  stale_after_seconds: "지연 판단 기준",
  last_synced_at: "마지막 동기화",
  last_warning_reason_code: "마지막 경고 사유",
  last_warning_message: "마지막 경고 메시지",
  metrics: "현재 노출",
  limits: "노출 한도",
  headroom: "추가 진입 여유",
  reference_symbol: "기준 심볼",
  reference_tier: "기준 리스크 티어",
  primary_regime: "주요 레짐",
  trend_alignment: "추세 정렬",
  volatility_regime: "변동성 레짐",
  volume_regime: "거래량 레짐",
  momentum_state: "모멘텀 상태",
  data_quality_flags: "데이터 품질 플래그",
  context_timeframes: "상위 타임프레임",
  adaptive_protection_mode: "적응형 보호 로직",
  signal_weight: "신호 가중치",
  confidence_multiplier: "신뢰도 배수",
  risk_pct_multiplier: "리스크 배수",
  hold_bias: "홀드 편향",
  gross_exposure_pct_equity: "총 노출 비중",
  long_exposure_pct_equity: "롱 노출 비중",
  short_exposure_pct_equity: "숏 노출 비중",
  directional_bias_pct: "방향 편중",
  decision_symbol_concentration_pct: "심볼 집중도",
  same_tier_concentration_pct: "동일 티어 집중도",
  largest_position_pct_equity: "최대 단일 포지션 비중",
  projected_trade_notional_pct_equity: "예상 신규 진입 노출",
  gross_exposure_pct: "총 노출 한도/여유",
  largest_position_pct: "최대 포지션 한도/여유",
  exposure_status: "노출 상태",
  execution_policy_key: "실행 정책",
};

const valueMap: Record<string, string> = {
  hold: "보류",
  long: "롱",
  short: "숏",
  reduce: "축소",
  exit: "청산",
  paused: "운영 일시 중지",
  live: "실거래",
  paper: "모의 운영",
  shadow: "그림자 점검",
  live_dry_run: "실거래 사전 점검",
  limited_live: "제한된 실거래",
  full_live: "실거래 전체 허용",
  live_ready: "실거래 가능",
  live_guarded: "진입 제한 모드",
  market_data_only: "시장 데이터만 수집",
  ai_active: "AI 활성",
  pending: "대기",
  partially_filled: "부분 체결",
  filled: "체결 완료",
  cancelled: "취소",
  canceled: "취소",
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
  critical: "치명적",
  small: "작음",
  large: "큼",
  monitor: "모니터링",
  act: "실행 권고",
  ok: "정상",
  info: "정보",
  warning: "경고",
  error: "오류",
  risk: "리스크",
  execution: "실행",
  approval_control: "승인/운영 제어",
  protection: "보호주문",
  health_system: "헬스/시스템",
  ai_decision: "AI/의사결정",
  ai: "AI",
  user: "사용자",
  manual_source: "수동",
  "deterministic-mock": "결정론 Mock",
  openai: "OpenAI",
  mock: "Mock",
  binance: "Binance",
  not_paused: "중지 아님",
  idle: "대기 중",
  waiting_cooldown: "재시도 대기",
  not_eligible: "자동 복구 대상 아님",
  blocked: "자동 복구 차단",
  ready: "복구 가능",
  resumed: "자동 복구 완료",
  recoverable_system: "일시 장애 복구형",
  manual_pause: "수동 중지",
  hard_risk_lock: "하드 리스크 잠금",
  config_block: "설정 차단",
  portfolio_unsafe: "포트폴리오 위험",
  readiness: "실주문 준비",
  pause: "운영 일시 중지",
  risk_block: "신규 진입 차단",
  auto_resume: "자동 복구",
  unknown: "미확인",
  TRADABLE: "거래 가능",
  PROTECTION_REQUIRED: "보호 복구 필요",
  DEGRADED_MANAGE_ONLY: "신규 진입 보류",
  EMERGENCY_EXIT: "비상 청산",
  PAUSED: "운영 일시 중지",
  recreating: "보호 주문 재생성 중",
  restored: "복구 완료",
  manage_only: "관리 전용 유지",
  protection_required: "보호 복구 필요",
  emergency_exit: "비상 청산 진행",
  protected: "보호됨",
  missing: "보호 확인 필요",
  stop_loss: "손절 주문",
  take_profit: "익절 주문",
  protected_recreated: "보호 재생성 완료",
  position_ready: "포지션 정상",
  flat: "포지션 없음",
  open_orders_failed: "미체결 주문 확인 실패",
  positions_failed: "포지션 조회 실패",
  state_inconsistent: "계좌 상태 불일치",
  market_data_stale: "시장 데이터 지연",
  market_data_unavailable: "시장 데이터 조회 실패",
  open_orders_unavailable: "보호 주문 조회 실패",
  positions_unavailable: "포지션 조회 실패",
  exchange_ledger_truth: "체결 ledger 기준",
  execution_ledger_truth: "체결 ledger 기준",
  exchange_synced: "거래소 기준 동기화 완료",
  fallback_reconciled: "보수적으로 맞춰 반영",
  stale_snapshot: "스냅샷 지연",
  exchange_confirmed: "거래소 계좌 기준",
  deterministic_delta_fallback: "이전 스냅샷 보정",
  stale: "조금 늦음",
  at_limit: "한도 도달",
  near_limit: "한도 근접",
  adaptive_atr_regime_aware: "ATR 레짐 적응형",
  bullish: "상승 레짐",
  bearish: "하락 레짐",
  range: "횡보 레짐",
  transition: "전환 레짐",
  bullish_aligned: "상승 정렬",
  bearish_aligned: "하락 정렬",
  mixed: "혼합",
  compressed: "압축",
  normal: "보통",
  expanded: "확장",
  weak: "약함",
  strong: "강함",
  strengthening: "강화",
  stable: "안정",
  weakening: "약화",
  overextended: "과열",
  NO_EDGE: "우위 없음",
  RANGE_CHOP: "횡보 잡음",
  TREND_UP: "상승 추세",
  BREAKOUT: "돌파",
  WEAK_VOLUME: "약한 거래량",
  MOMENTUM_WEAKENING: "모멘텀 약화",
  PROVIDER_OPENAI: "OpenAI 신호",
  PROVIDER_DETERMINISTIC_MOCK: "결정론 Mock 신호",
  trading_decision: "거래 의사결정",
  integration_planner: "통합 기획",
  ui_ux: "UI/UX",
};

const reasonCodeMap: Record<string, string> = {
  ENTRY_AUTO_RESIZED: "진입 수량이 자동 축소 승인되었습니다.",
  ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT: "총 노출 한도에 맞게 진입 수량이 축소되었습니다.",
  ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT: "방향 편향 한도에 맞게 진입 수량이 축소되었습니다.",
  ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT: "최대 단일 포지션 한도에 맞게 진입 수량이 축소되었습니다.",
  ENTRY_CLAMPED_TO_SAME_TIER_LIMIT: "동일 티어 집중도 한도에 맞게 진입 수량이 축소되었습니다.",
  TRADING_PAUSED: "거래가 일시 중지 상태여서 신규 진입이 차단되었습니다.",
  STALE_MARKET_DATA: "시장 데이터가 지연되어 신규 진입이 차단되었습니다.",
  INCOMPLETE_MARKET_DATA: "시장 데이터가 불완전하여 신규 진입이 차단되었습니다.",
  DAILY_LOSS_LIMIT_REACHED: "일일 손실 한도에 도달해 추가 진입이 차단되었습니다.",
  MAX_CONSECUTIVE_LOSSES_REACHED: "연속 손실 한도에 도달해 보수적으로 제한합니다.",
  LEVERAGE_EXCEEDS_LIMIT: "레버리지가 허용 한도를 초과했습니다.",
  RISK_PCT_EXCEEDS_LIMIT: "거래당 리스크 비중이 허용 한도를 초과했습니다.",
  MISSING_STOP_OR_TARGET: "손절 또는 익절 값이 없어 진입이 차단되었습니다.",
  INVALID_LONG_BRACKETS: "롱 포지션 보호 가격 구조가 유효하지 않습니다.",
  INVALID_SHORT_BRACKETS: "숏 포지션 보호 가격 구조가 유효하지 않습니다.",
  SLIPPAGE_THRESHOLD_EXCEEDED: "슬리피지가 허용 범위를 초과했습니다.",
  HOLD_DECISION: "현재 판단은 HOLD입니다.",
  LIVE_ENV_DISABLED: "실거래 환경 플래그가 꺼져 있습니다.",
  LIVE_TRADING_DISABLED: "실거래 사용 설정이 꺼져 있습니다.",
  ROLLOUT_MODE_SHADOW: "그림자 점검 단계라 실제 주문은 보내지 않습니다.",
  ROLLOUT_MODE_LIVE_DRY_RUN: "실거래 사전 점검 단계라 실제 주문은 보내지 않습니다.",
  LIVE_APPROVAL_POLICY_DISABLED: "수동 승인 정책이 꺼져 있습니다.",
  LIVE_APPROVAL_REQUIRED: "실거래 승인 창이 열려 있지 않습니다.",
  LIVE_CREDENTIALS_MISSING: "실거래 API Key 또는 Secret이 없습니다.",
  EXCHANGE_ACCOUNT_STATE_UNAVAILABLE: "거래소 계좌 상태를 확인할 수 없습니다.",
  EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE: "거래소 또는 네트워크 연결이 일시적으로 불안정합니다.",
  TEMPORARY_MARKET_DATA_FAILURE: "시장 데이터 확인 중 일시 장애가 발생했습니다.",
  TEMPORARY_SYNC_FAILURE: "거래소 상태 동기화에 일시 장애가 발생했습니다.",
  EXCHANGE_POSITION_SYNC_FAILED: "거래소 포지션 상태를 동기화하지 못했습니다.",
  EXCHANGE_OPEN_ORDERS_SYNC_FAILED: "거래소 미체결 주문 상태를 동기화하지 못했습니다.",
  MANUAL_USER_REQUEST: "운영자가 수동으로 거래를 중지했습니다.",
  HARD_RISK_LOCK_DAILY_LOSS: "일일 손실 하드 락이 걸려 자동 복구가 금지됩니다.",
  HARD_RISK_LOCK_CONSECUTIVE_LOSS: "연속 손실 하드 락이 걸려 자동 복구가 금지됩니다.",
  PROTECTIVE_ORDER_FAILURE: "보호 주문 생성 실패로 자동 복구가 금지됩니다.",
  MISSING_PROTECTIVE_ORDERS: "미보호 포지션이 감지되어 자동 복구가 금지됩니다.",
  PROTECTION_REQUIRED: "미보호 포지션이 감지되어 보호 복구가 우선입니다.",
  DEGRADED_MANAGE_ONLY: "보호 복구가 반복 실패해 신규 진입 보류 상태로 전환되었습니다.",
  EMERGENCY_EXIT: "비상 청산 상태가 진행 중이라 신규 진입이 차단됩니다.",
  INVALID_PROTECTION_BRACKETS: "보호 복구용 손절/익절 값이 현재 포지션 방향과 맞지 않습니다.",
  PORTFOLIO_RISK_UNCERTAIN: "포트폴리오 위험 상태를 신뢰할 수 없습니다.",
  ACCOUNT_STATE_INCONSISTENT: "로컬 상태와 거래소 상태가 일치하지 않습니다.",
  AI_DISABLED: "AI 사용이 꺼져 있어 자동 판단을 건너뜁니다.",
  GROSS_EXPOSURE_LIMIT_REACHED: "총 노출 한도를 초과했습니다.",
  LARGEST_POSITION_LIMIT_REACHED: "최대 단일 포지션 한도를 초과했습니다.",
  DIRECTIONAL_BIAS_LIMIT_REACHED: "방향 편향 한도를 초과했습니다.",
  SAME_TIER_CONCENTRATION_LIMIT_REACHED: "동일 티어 집중도 한도를 초과했습니다.",
  protection_verification_failed: "보호 주문 검증에 실패했습니다.",
  protection_recreate_attempted: "보호 주문 재생성을 시도했습니다.",
  protection_recreate_failed: "보호 주문 재생성에 실패했습니다.",
  unprotected_position_detected: "미보호 포지션이 감지되었습니다.",
  emergency_exit_triggered: "비상 청산이 시작되었습니다.",
  emergency_exit_completed: "비상 청산이 완료되었습니다.",
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
  "gross_exposure_pct_equity",
  "long_exposure_pct_equity",
  "short_exposure_pct_equity",
  "directional_bias_pct",
  "decision_symbol_concentration_pct",
  "same_tier_concentration_pct",
  "largest_position_pct_equity",
  "projected_trade_notional_pct_equity",
  "gross_exposure_pct",
  "largest_position_pct",
  "signal_weight",
  "confidence_multiplier",
  "risk_pct_multiplier",
  "hold_bias",
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
  "net_realized_pnl",
  "unrealized_pnl",
  "fee_paid",
  "entry_price",
  "mark_price",
  "liquidation_price",
  "notional",
  "wallet_balance",
  "margin_balance",
  "available_balance",
  "equity",
  "cash_balance",
]);

const leverageKeys = new Set(["leverage", "approved_leverage", "max_leverage"]);
const durationKeys = new Set(["freshness_seconds", "stale_after_seconds"]);
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
  "pause_triggered_at",
  "auto_resume_after",
  "generated_at",
  "exchange_update_time",
  "update_time",
  "last_synced_at",
  "last_market_refresh_at",
  "last_position_management_at",
  "last_decision_at",
  "last_ai_decision_at",
  "next_market_refresh_due_at",
  "next_position_management_due_at",
  "next_decision_due_at",
  "next_ai_call_due_at",
]);

const isoDatePattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/;

function humanizeKey(key: string) {
  return key.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function translateString(value: string) {
  return lookupRiskReasonCode(value) ?? reasonCodeMap[value] ?? valueMap[value] ?? value;
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
  if (key === "allowed") return value ? "허용" : "차단";
  if (key === "schema_valid") return value ? "정상" : "실패";
  if (key === "trading_paused") return value ? "중지됨" : "운영 중";
  if (key === "exchange_can_trade") return value ? "가능" : "차단";
  if (key === "app_live_armed") return value ? "준비됨" : "해제됨";
  if (key === "approval_window_open") return value ? "열림" : "닫힘";
  if (key === "paused") return value ? "중지됨" : "운영 중";
  if (key === "degraded") return value ? "신규 진입 보류" : "정상";
  if (key === "risk_allowed") return value ? "허용" : "차단";
  if (key?.endsWith("_configured")) return value ? "설정됨" : "미설정";
  if (key?.endsWith("_enabled")) return value ? "사용 중" : "꺼짐";
  if (key === "auto_resume_whitelisted" || key === "auto_resume_eligible" || key === "protected") {
    return value ? "예" : "아니오";
  }
  if (key === "has_stop_loss" || key === "has_take_profit") {
    return value ? "있음" : "없음";
  }
  return value ? "예" : "아니오";
}

function formatNumber(key: string | undefined, value: number) {
  if (durationKeys.has(key ?? "")) {
    return `${value.toLocaleString("ko-KR")}초`;
  }
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
      maximumFractionDigits: 4,
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
