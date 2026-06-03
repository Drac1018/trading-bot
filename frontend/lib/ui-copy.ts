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
  event_type: "이벤트",
  event_label: "이벤트",
  entity_type: "대상 유형",
  entity_type_label: "대상 유형",
  entity_id: "대상 ID",
  message: "메시지",
  message_label: "메시지",
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
  allowed: "허용 여부",
  confidence: "신뢰도",
  decision_quality: "AI 비용 / edge / 결과",
  ai_trigger_reason: "AI 호출 분류",
  ai_review_type: "AI 호출 분류",
  ai_trigger_reason_codes: "AI 호출 세부 코드",
  ai_review: "AI 검토 사유",
  ai_skip_reason: "AI 검토 생략 사유",
  last_ai_skip_reason: "AI 검토 생략 사유",
  provider_status: "AI 공급자 상태",
  provider_invoked: "AI 공급자 호출",
  provider_skipped: "AI 공급자 생략",
  market_signal_context: "당시 시장 신호",
  ai_trigger_summary: "당시 시장 신호 요약",
  market_signal_summary: "당시 시장 신호 요약",
  macro_event_context_summary: "이벤트 리스크",
  macro_event_risk_summary: "이벤트 리스크",
  risk_guard_result: "리스크 가드 결과",
  next_event_name: "다음 이벤트",
  next_event_importance: "이벤트 중요도",
  minutes_to_next_event: "이벤트까지 남은 시간",
  active_risk_window: "이벤트 리스크 구간",
  release_reaction_window: "발표 직후 변동성 구간",
  enrichment_vendors: "실제값 반영 출처",
  bls_actual_enriched: "BLS 실제값 반영",
  bea_actual_enriched: "BEA 실제값 반영",
  suppression_active: "진입 제안 억제",
  suppression_reason_code: "진입 제안 억제 사유",
  allow_same_side_add_on: "same-side add-on 허용",
  allowed_add_on_side: "허용 add-on 방향",
  latest_price: "현재가",
  latest_volume: "최근 거래량",
  stop_loss: "손절가",
  take_profit: "익절가",
  leverage: "레버리지",
  approved_leverage: "승인 레버리지",
  risk_pct: "리스크 비중",
  approved_risk_pct: "승인 리스크 비중",
  rationale_codes: "근거 코드",
  capacity_reason: "수용 한도 사유",
  trigger_event: "AI 호출 이벤트",
  trigger_fingerprint: "AI 호출 지문",
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
  asset: "자산",
  available_balance: "사용 가능 잔고",
  total_wallet_balance: "총 지갑 잔고",
  wallet_balance: "지갑 잔고",
  total_unrealized_profit: "미실현 손익 합계",
  unrealized_profit: "미실현 손익",
  total_margin_balance: "마진 잔고 합계",
  margin_balance: "마진 잔고",
  max_withdraw_amount: "출금 가능 최대 금액",
  data_source: "데이터 기준",
  cache_status: "캐시 상태",
  cache_stale: "캐시 지연 여부",
  cache_age_minutes: "캐시 경과 시간(분)",
  cache_refreshed_at: "캐시 갱신 시각",
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
  last_ai_decision_at: "최근 AI 호출",
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
  hold: "신규 진입 대기",
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
  skipped_pre_ai: "AI 검토 생략",
  invoked: "AI 검토 실행",
  deduped: "중복 생략",
  not_invoked: "AI 호출 없음",
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
  entry_candidate_review: "신규 진입 후보 검토",
  breakout_exception_review: "돌파 예외 검토",
  protection_review: "보호 상태 점검",
  manual_review: "수동 검토",
  open_position_review: "오픈 포지션 점검",
  periodic_backstop_review: "주기 백스톱 검토",
  entry_candidate_event: "진입 후보 이벤트",
  breakout_exception_event: "브레이크아웃 예외 이벤트",
  protection_review_event: "보호 상태 점검",
  manual_review_event: "수동 검토",
  external_api: "외부 API",
  fixture: "테스트 fixture",
  stub: "stub",
  unavailable: "사용 불가",
  incomplete: "불완전",
  fred: "FRED",
  bls: "BLS",
  bea: "BEA",
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
  ENTRY_CANDIDATE_SELECTED: "신규 진입 후보 선정",
  ENTRY_CANDIDATE_WEAK_VOLUME_PREAI: "거래량 부족으로 AI 검토 생략",
  ENTRY_CANDIDATE_NEUTRAL_CONTEXT_HOLD_BACKOFF: "반복 중립 후보라 AI 검토 생략",
  ENTRY_CANDIDATE_NEUTRAL_CONTEXT_PREAI: "중립 신호라 AI 검토 생략",
  ENTRY_CANDIDATE_LOW_ACTIONABILITY_HOLD_BACKOFF: "반복 저효용 후보라 AI 검토 생략",
  ENTRY_CANDIDATE_ORDER_PATH_NOT_ACTIONABLE: "주문 경로 미준비로 AI 검토 생략",
  ENTRY_CANDIDATE_ACTIVE_PENDING_PLAN_PREAI: "기존 대기 진입안으로 AI 검토 생략",
  ENTRY_CANDIDATE_INCOMPLETE_TRADE_PLAN_PREAI: "진입 구조 불완전으로 AI 검토 생략",
  ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN: "최근 같은 장면의 AI hold 판단 재사용",
  AI_ENTRY_OUTPUT_INCOMPLETE: "AI 진입안 구조 불완전으로 hold 정규화",
  ROLE_DAILY_TOKEN_BUDGET_EXHAUSTED: "일일 AI 토큰 예산 소진",
  SOFT_SIGNAL_AI_REVIEW: "약한 후보 AI 검토 대상",
  SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE: "약한 관망 후보라 AI 호출 전 억제",
  SOFT_SIGNAL_REVIEW_COOLDOWN_ACTIVE: "약한 후보 전환감시 쿨다운",
  SOFT_SIGNAL_REVIEW_NO_MATERIAL_CHANGE: "약한 후보 변화 부족으로 AI 생략",
  AI_CYCLE_BUDGET_EXHAUSTED: "사이클 AI 예산 초과로 신규 후보 검토 생략",
  SOFT_SIGNAL_TRANSITION_WATCH: "약한 후보 전환감시",
  SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_WATCH: "직접 진입 대신 대기 계획",
  SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_HOLD: "약한 후보 직접 진입 차단",
  DERIVATIVES_ALIGNMENT_HEADWIND: "파생시장 정합성 부족",
  BREAKOUT_OI_SPREAD_FILTER: "돌파 OI/스프레드 조건 부족",
  BREAKOUT_OI_NOT_EXPANDING: "돌파 OI 확장 없음",
  MACRO_EVENT_RISK_WINDOW_ACTIVE: "거시 이벤트 리스크 구간",
  MACRO_EVENT_IMMINENT: "주요 경제 이벤트 임박으로 신규 진입 보수화",
  MACRO_RELEASE_REACTION_WINDOW: "발표 직후 변동성 구간",
  MACRO_EVENT_CONTEXT_STALE: "이벤트 데이터 지연",
  MACRO_EVENT_CONTEXT_INCOMPLETE: "이벤트 데이터 불완전",
  MACRO_EVENT_ENRICHMENT_AVAILABLE: "경제지표 실제값 반영 가능",
  MACRO_EVENT_RESULT_AVAILABLE: "경제 이벤트 발표치 반영",
  MACRO_EVENT_RESULT_VS_FORECAST: "발표치가 예상치와 다름",
  MACRO_EVENT_RESULT_VS_PRIOR: "발표치가 이전치와 다름",
  MACRO_EVENT_RESULT_BEARISH: "발표 결과가 위험자산에 부담",
  MACRO_EVENT_RESULT_BULLISH: "발표 결과가 위험자산에 우호적",
  MACRO_EVENT_RESULT_NEUTRAL: "발표 결과 방향성 중립",
  MACRO_EVENT_RESULT_SURPRISE_HIGH: "발표 결과 서프라이즈 큼",
  MACRO_EVENT_RESULT_CONFLICT: "발표 결과와 진입 방향 충돌",
  ENTRY_AUTO_RESIZED: "진입 수량이 자동 축소 승인되었습니다.",
  ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT: "총 노출 한도에 맞게 진입 수량이 축소되었습니다.",
  ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT: "방향 편향 한도에 맞게 진입 수량이 축소되었습니다.",
  ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT: "최대 단일 포지션 한도에 맞게 진입 수량이 축소되었습니다.",
  ENTRY_CLAMPED_TO_SAME_TIER_LIMIT: "동일 티어 집중도 한도에 맞게 진입 수량이 축소되었습니다.",
  PLAN_CANCELED_NO_ENTRY_CAPACITY: "이미 열린 포지션 때문에 추가 진입 여유가 없어 대기 플랜 감시를 중단했습니다.",
  TRADING_PAUSED: "시스템 가드 모드로 신규 진입을 보류했습니다.",
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
  HOLD_DECISION: "현재는 신규 진입 신호가 없어 대기 중입니다.",
  LIVE_ENV_DISABLED: "실거래 환경 플래그가 꺼져 있습니다.",
  LIVE_TRADING_DISABLED: "실거래 사용 설정이 꺼져 있습니다.",
  ROLLOUT_MODE_SHADOW: "그림자 점검 단계라 실제 주문은 보내지 않습니다.",
  ROLLOUT_MODE_LIVE_DRY_RUN: "실거래 사전 점검 단계라 실제 주문은 보내지 않습니다.",
  LIVE_APPROVAL_POLICY_DISABLED: "수동 승인 정책이 꺼져 있습니다.",
  LIVE_APPROVAL_REQUIRED: "실거래 승인 창이 닫혀 있어 신규 진입을 보류했습니다.",
  LIVE_CREDENTIALS_MISSING: "실거래 API Key 또는 Secret이 없습니다.",
  EXCHANGE_ACCOUNT_STATE_UNAVAILABLE: "거래소 계좌 상태를 확인할 수 없습니다.",
  EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE: "거래소 또는 네트워크 연결이 일시적으로 불안정합니다.",
  TEMPORARY_MARKET_DATA_FAILURE: "시장 데이터 확인 중 일시 장애가 발생했습니다.",
  TEMPORARY_SYNC_FAILURE: "거래소 상태 동기화에 일시 장애가 발생했습니다.",
  EXCHANGE_POSITION_SYNC_FAILED: "거래소 포지션 상태를 동기화하지 못했습니다.",
  EXCHANGE_OPEN_ORDERS_SYNC_FAILED: "거래소 미체결 주문 상태를 동기화하지 못했습니다.",
  UNRESOLVED_SUBMISSION_GUARD_ACTIVE: "결과가 확인되지 않은 주문 제출이 있어 신규 진입을 차단합니다.",
  UNRESOLVED_SUBMISSION_DEADLINE_EXCEEDED: "미해결 주문 제출 확인 시간이 초과되어 신규 진입을 차단합니다.",
  LIVE_ORDER_SUBMISSION_UNKNOWN: "주문 제출 결과가 불명확해 신규 진입을 차단합니다.",
  BINANCE_REST_CIRCUIT_OPEN: "Binance REST 회로가 열려 거래소 상태 확인을 제한합니다.",
  BINANCE_REST_RECOVERING_SYNC_STALE: "Binance REST 복구 중이며 동기화 상태가 아직 오래되었습니다.",
  BINANCE_REST_RECOVERING_SYNC_REQUIRED: "Binance REST 복구 후 거래소 상태 재동기화가 필요합니다.",
  BINANCE_REST_TRANSPORT_ERROR: "Binance REST 전송 오류가 감지되었습니다.",
  BINANCE_REST_SERVER_ERROR: "Binance REST 서버 오류가 감지되었습니다.",
  BINANCE_REST_RATE_LIMITED: "Binance REST rate limit으로 요청이 제한되었습니다.",
  BINANCE_REST_MUTATING_ORDER_FAILED: "Binance REST 주문 변경 요청이 실패했습니다.",
  DRAWDOWN_STATE_CAUTION: "손실/드로다운 주의 상태라 진입 크기를 보수적으로 제한합니다.",
  DRAWDOWN_STATE_CONTAINMENT: "드로다운 억제 상태라 신규 진입을 더 강하게 제한합니다.",
  DRAWDOWN_STATE_RECOVERY: "회복 상태라 신규 진입 리스크를 낮춰 운용합니다.",
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
  LARGEST_POSITION_LIMIT_REACHED: "요청 수량이 단일 심볼 한도를 초과했습니다.",
  DETERMINISTIC_BASELINE_DISAGREEMENT: "AI 최종 판단과 결정론적 기준선이 달라 즉시 주문을 보류했습니다.",
  DIRECTIONAL_BIAS_LIMIT_REACHED: "방향 편향 한도를 초과했습니다.",
  SAME_TIER_CONCENTRATION_LIMIT_REACHED: "동일 티어 집중도 한도를 초과했습니다.",
  REPLACED_BY_NEW_APPROVED_PLAN: "더 최신 승인 플랜으로 대체되어 이전 대기 플랜 감시를 중단했습니다.",
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
  return reasonCodeMap[value] ?? lookupRiskReasonCode(value) ?? valueMap[value] ?? value;
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
  if (key === "suppression_active") return value ? "활성" : "비활성";
  if (key === "allow_same_side_add_on") return value ? "허용" : "불가";
  if (key === "schema_valid") return value ? "정상" : "실패";
  if (key === "trading_paused") return value ? "중지됨" : "운영 중";
  if (key === "cache_stale") return value ? "오래됨" : "정상";
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

export type MacroEventContextSummary = {
  source_status?: string | null;
  source_vendor?: string | null;
  next_event_name?: string | null;
  next_event_importance?: string | null;
  minutes_to_next_event?: number | null;
  active_risk_window?: boolean | null;
  release_reaction_window?: boolean | null;
  is_stale?: boolean | null;
  is_complete?: boolean | null;
  is_incomplete?: boolean | null;
  affected_assets?: string[] | null;
  enrichment_vendors?: string[] | null;
  bls_actual_enriched?: boolean | null;
  bea_actual_enriched?: boolean | null;
  event_risk_active?: boolean | null;
  event_risk_reason_codes?: string[] | null;
  event_bias_used?: string | null;
};

function asMacroEventContext(value: unknown): MacroEventContextSummary | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as MacroEventContextSummary;
}

function hasMacroEventContext(value: MacroEventContextSummary | null) {
  if (!value) {
    return false;
  }
  return Boolean(
    value.source_status ||
      value.next_event_name ||
      value.event_risk_active ||
      (Array.isArray(value.event_risk_reason_codes) && value.event_risk_reason_codes.length > 0),
  );
}

function macroEventDataUncertain(value: MacroEventContextSummary) {
  return Boolean(
    value.is_stale ||
      value.is_incomplete ||
      value.is_complete === false ||
      ["stale", "incomplete", "unavailable", "error"].includes(String(value.source_status ?? "")),
  );
}

function macroEventMinuteText(minutes: number) {
  if (minutes < 0) {
    return `${Math.abs(minutes)}분 전 발표`;
  }
  return `${minutes}분 전`;
}

export function formatMacroEventContextSummary(value: unknown): string {
  const context = asMacroEventContext(value);
  if (!hasMacroEventContext(context) || !context) {
    return "이벤트 정보 없음";
  }
  if (macroEventDataUncertain(context)) {
    return "이벤트 데이터 지연/불완전";
  }
  if (context.release_reaction_window) {
    return "발표 직후 변동성 구간";
  }
  const eventName = context.next_event_name?.trim();
  const isHighImpact = context.next_event_importance === "high";
  const minutes = typeof context.minutes_to_next_event === "number" ? context.minutes_to_next_event : null;
  if (minutes !== null && minutes < 0 && context.active_risk_window) {
    return "발표 직후 변동성 구간";
  }
  if (minutes !== null) {
    const prefix = isHighImpact ? "주요 경제 이벤트" : eventName ?? "경제 이벤트";
    return `${prefix} ${macroEventMinuteText(minutes)}`;
  }
  if (context.active_risk_window) {
    return "이벤트 리스크 구간";
  }
  return eventName ?? "이벤트 정보 있음";
}

export function formatMacroEventContextDetail(value: unknown): string {
  const context = asMacroEventContext(value);
  if (!hasMacroEventContext(context) || !context) {
    return "AI 판단 당시 이벤트 컨텍스트 없음";
  }
  const parts: string[] = [];
  if (macroEventDataUncertain(context)) {
    parts.push("이벤트 데이터 지연/불완전");
  }
  if (context.next_event_name) {
    parts.push(context.next_event_name);
  }
  if (context.next_event_importance) {
    parts.push(`중요도 ${formatDisplayValue(context.next_event_importance)}`);
  }
  if (context.active_risk_window) {
    parts.push("이벤트 리스크 구간 활성");
  }
  if (context.release_reaction_window) {
    parts.push("발표 직후 변동성 구간");
  }
  if (context.bls_actual_enriched) {
    parts.push("BLS 실제값 반영됨");
  }
  if (context.bea_actual_enriched) {
    parts.push("BEA 실제값 반영됨");
  }
  if (context.source_status) {
    const vendor = context.source_vendor ? ` / ${formatDisplayValue(context.source_vendor)}` : "";
    parts.push(`source ${formatDisplayValue(context.source_status)}${vendor}`);
  }
  if (context.affected_assets && context.affected_assets.length > 0) {
    parts.push(`자산 ${context.affected_assets.join(", ")}`);
  }
  if (context.event_risk_reason_codes && context.event_risk_reason_codes.length > 0) {
    parts.push(`사유 ${context.event_risk_reason_codes.map((code) => formatDisplayValue(code)).join(", ")}`);
  }
  return parts.join(" / ");
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
  const asset = typeof row.asset === "string" ? row.asset : null;
  if (symbol && timeframe) {
    return `${symbol} / ${timeframe}`;
  }
  if (asset) {
    return asset;
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
  if (typeof row.id === "number" || typeof row.id === "string") {
    return `ID ${formatDisplayValue(row.id, "id")}`;
  }
  return `데이터 항목 ${index + 1}`;
}

export function formatListValue(value: unknown, key?: string): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => formatDisplayValue(item, key));
}
