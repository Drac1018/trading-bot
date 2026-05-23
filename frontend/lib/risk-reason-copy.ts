export type ReasonCodeCategory = "operational_control" | "entry_wait" | "safety_block" | "unknown";

export type ReasonCodeDisplay = {
  raw_code: string;
  category: ReasonCodeCategory;
  title_ko: string;
  detail_ko: string;
  auto_clear_hint_ko: string;
  operator_action_ko: string;
  check_location_ko: string;
  known: boolean;
};

type ReasonCodeDefinition = Omit<ReasonCodeDisplay, "raw_code" | "known">;

const entryWaitReasonDefinitions: Record<string, ReasonCodeDefinition> = {
  HOLD_DECISION: {
    category: "entry_wait",
    title_ko: "신규 진입 신호가 없어 대기 중입니다",
    detail_ko: "현재 AI 판단은 새 포지션을 열 만큼의 진입 신호가 없다고 봤습니다.",
    auto_clear_hint_ko: "다음 판단 주기에서 진입 신호가 생기면 자동으로 다시 검토됩니다.",
    operator_action_ko: "운영 상태가 정상이라면 별도 조치 없이 다음 판단 주기를 기다립니다.",
    check_location_ko: "AI 의견 / 이번 판단 주기 상태",
  },
  DERIVATIVES_ALIGNMENT_HEADWIND: {
    category: "entry_wait",
    title_ko: "파생시장 정합성이 진입 방향을 뒷받침하지 않습니다",
    detail_ko: "가격 움직임은 있어도 funding, basis, taker flow, 포지션 쏠림 같은 파생시장 근거가 진입 방향과 충분히 맞지 않습니다.",
    auto_clear_hint_ko: "파생시장 정합성이 개선되면 다음 판단 주기에서 다시 진입 후보로 평가됩니다.",
    operator_action_ko: "AI 의견의 derivatives summary와 시장 신호 요약에서 funding, basis, taker flow, crowding 상태를 확인하세요.",
    check_location_ko: "AI 의견 / 파생시장 요약",
  },
  BREAKOUT_OI_SPREAD_FILTER: {
    category: "entry_wait",
    title_ko: "돌파처럼 보여도 OI와 스프레드 조건이 부족합니다",
    detail_ko: "가격은 돌파 형태를 보였지만 OI 확장 또는 스프레드 품질이 충분하지 않아 추격 진입을 보류했습니다.",
    auto_clear_hint_ko: "OI 확장과 체결 품질 조건이 좋아지면 다음 판단 주기에서 다시 평가됩니다.",
    operator_action_ko: "AI 의견의 돌파 근거, OI 확장 여부, 스프레드 부담 여부를 확인하세요.",
    check_location_ko: "AI 의견 / 시장 신호 / 파생시장 요약",
  },
  BREAKOUT_OI_NOT_EXPANDING: {
    category: "entry_wait",
    title_ko: "돌파 확인에 필요한 OI 증가가 없습니다",
    detail_ko: "가격 돌파가 신규 포지션 유입으로 확인되지 않아 돌파 추종 진입을 보류했습니다.",
    auto_clear_hint_ko: "돌파 방향으로 OI가 확장되면 다음 판단 주기에서 다시 평가됩니다.",
    operator_action_ko: "AI 의견의 OI 확장 여부와 돌파 방향 신뢰도를 확인하세요.",
    check_location_ko: "AI 의견 / 파생시장 요약",
  },
  ENTRY_TRIGGER_NOT_MET: {
    category: "entry_wait",
    title_ko: "진입 조건이 아직 충족되지 않았습니다",
    detail_ko: "가격이 진입 구간에 들어오거나 확인 캔들이 만들어져야 합니다.",
    auto_clear_hint_ko: "조건이 맞으면 다음 판단 주기에서 자동 재검토됩니다.",
    operator_action_ko: "진입 대기 계획의 진입 구간과 확인 조건을 확인합니다.",
    check_location_ko: "진입 대기 계획 > 진입 구간 / 확인 조건",
  },
  CHASE_LIMIT_EXCEEDED: {
    category: "entry_wait",
    title_ko: "가격이 이미 지나가 추격 진입을 막았습니다",
    detail_ko: "현재가는 계획한 진입 구간보다 멀어졌습니다. 지금 따라 들어가면 손익비가 나빠질 수 있어 신규 주문을 보류합니다.",
    auto_clear_hint_ko: "가격이 진입 허용 범위로 돌아오면 다음 판단 주기에서 자동 재검토됩니다.",
    operator_action_ko: "관측 추격폭이 허용 추격폭보다 큰지 확인합니다.",
    check_location_ko: "진입 대기 계획 > 관측 추격폭 / 허용 추격폭",
  },
  PLAN_MAX_CHASE_EXCEEDED: {
    category: "entry_wait",
    title_ko: "대기 중인 진입 계획이 추격 한도를 넘었습니다",
    detail_ko: "대기 계획은 유지되지만, 현재 가격에서는 주문으로 전환하지 않습니다.",
    auto_clear_hint_ko: "가격이 다시 허용 범위로 돌아오면 watcher가 다음 주기에서 다시 평가합니다.",
    operator_action_ko: "대기 계획의 현재가, 진입 구간, 최대 추격폭을 확인합니다.",
    check_location_ko: "진입 대기 계획 > 감시 상태 / 추격 폭",
  },
  PLAN_LATE_CHASE_WAITING_REENTRY: {
    category: "entry_wait",
    title_ko: "가격이 늦게 따라붙어 재진입 구간을 기다립니다",
    detail_ko: "진입 계획은 남아 있지만 늦은 추격 상태라 바로 주문하지 않고 더 좋은 재진입 조건을 기다립니다.",
    auto_clear_hint_ko: "재진입 조건이 맞으면 다음 watcher 판단에서 다시 검토됩니다.",
    operator_action_ko: "진입 대기 계획의 재진입 조건과 추격폭을 확인합니다.",
    check_location_ko: "진입 대기 계획 > 감시 상태 / 추격 폭",
  },
  PLAN_CONFIRM_QUALITY_LOW: {
    category: "entry_wait",
    title_ko: "확인 신호 품질이 부족합니다",
    detail_ko: "진입 구간 접근은 감지됐지만 캔들 구조, 손익비, 추격폭 조건이 충분하지 않습니다.",
    auto_clear_hint_ko: "확인 신호 품질이 기준을 넘으면 다음 watcher 판단에서 다시 검토됩니다.",
    operator_action_ko: "진입 대기 계획의 품질 상태와 확인 조건을 확인합니다.",
    check_location_ko: "진입 대기 계획 > 품질 상태 / 확인 조건",
  },
  DETERMINISTIC_BASELINE_DISAGREEMENT: {
    category: "entry_wait",
    title_ko: "AI 판단과 기준선 판단이 달라 즉시 주문을 보류했습니다",
    detail_ko: "AI는 진입을 제안했지만, 규칙 기반 기준선과 일치하지 않아 안전상 즉시 주문하지 않았습니다.",
    auto_clear_hint_ko: "다음 판단 주기에서 판단이 일치하면 해소될 수 있습니다.",
    operator_action_ko: "AI 의견과 기준선 판단이 같은 방향인지 확인합니다.",
    check_location_ko: "AI 의견 / 신규 진입 판단",
  },
  CONFIDENCE_BELOW_MIN_ENTRY_THRESHOLD: {
    category: "entry_wait",
    title_ko: "진입 신뢰도가 기준보다 낮아 대기 중입니다",
    detail_ko: "AI가 감시용 진입 계획은 제안했지만 신뢰도가 최소 진입 기준보다 낮아 대기 진입 계획을 만들지 않았습니다.",
    auto_clear_hint_ko: "다음 판단 주기에서 신뢰도가 기준 이상으로 올라가면 다시 리스크 검토됩니다.",
    operator_action_ko: "AI 의견의 신뢰도, 감시용 진입 계획, 예상 비용 점검 기준을 확인합니다.",
    check_location_ko: "AI 의견 / 리스크 점검 > 예상 비용 점검",
  },
  NO_EDGE: {
    category: "entry_wait",
    title_ko: "거래 우위가 부족해 대기 중입니다",
    detail_ko: "현재 조건에서는 기대 손익이 충분하지 않아 신규 진입을 보류합니다.",
    auto_clear_hint_ko: "시장 조건과 기대값이 개선되면 다음 판단 주기에서 다시 검토됩니다.",
    operator_action_ko: "시장 신호와 기대값 요약을 확인합니다.",
    check_location_ko: "시장 신호 / 신규 진입 판단",
  },
  RANGE_CHOP: {
    category: "entry_wait",
    title_ko: "박스권 잡음이 커서 대기 중입니다",
    detail_ko: "현재 구간은 방향성이 약해 진입 후 흔들림 위험이 큽니다.",
    auto_clear_hint_ko: "방향성 또는 확인 신호가 생기면 다음 판단 주기에서 다시 검토됩니다.",
    operator_action_ko: "시장 레짐과 추세 정렬을 확인합니다.",
    check_location_ko: "시장 신호 / AI 의견",
  },
  WEAK_VOLUME: {
    category: "entry_wait",
    title_ko: "거래량 확인이 약해 대기 중입니다",
    detail_ko: "진입 방향을 뒷받침할 거래량 근거가 충분하지 않습니다.",
    auto_clear_hint_ko: "거래량 확인이 개선되면 다음 판단 주기에서 다시 검토됩니다.",
    operator_action_ko: "시장 신호의 거래량 상태를 확인합니다.",
    check_location_ko: "시장 신호 / 신규 진입 판단",
  },
  MOMENTUM_WEAKENING: {
    category: "entry_wait",
    title_ko: "모멘텀이 약해져 대기 중입니다",
    detail_ko: "진입 방향의 힘이 약해져 지금 주문하면 기대 손익이 나빠질 수 있습니다.",
    auto_clear_hint_ko: "모멘텀이 회복되면 다음 판단 주기에서 다시 검토됩니다.",
    operator_action_ko: "시장 신호의 모멘텀 상태를 확인합니다.",
    check_location_ko: "시장 신호 / 신규 진입 판단",
  },
};

const operationalReasonDefinitions: Record<string, ReasonCodeDefinition> = {
  ROLE_DAILY_TOKEN_BUDGET_EXHAUSTED: {
    category: "operational_control",
    title_ko: "trading_decision 일일 AI 토큰 예산을 모두 사용했습니다",
    detail_ko: "OpenAI 호출 예산이 소진되어 이번 주기는 규칙 기반 판단으로 처리됐습니다. 주문 실행 차단 사유가 아니라 AI 호출 생략 사유입니다.",
    auto_clear_hint_ko: "역할별 24시간 토큰 사용량이 예산 아래로 내려가면 자동으로 다시 OpenAI 호출이 가능합니다.",
    operator_action_ko: "운영 진단의 AI 비용/억제 상태에서 trading_decision 역할 예산과 재시도 가능 시간을 확인하세요.",
    check_location_ko: "운영 진단 > 리스크 > AI 비용/억제 상태",
  },
  MANUAL_USER_REQUEST: {
    category: "operational_control",
    title_ko: "운영자가 수동으로 거래를 일시정지했습니다",
    detail_ko: "운영자 요청으로 자동 신규 진입이 보류된 상태입니다.",
    auto_clear_hint_ko: "운영자가 resume하거나 정해진 자동 복구 조건이 통과해야 해소됩니다.",
    operator_action_ko: "설정 > 실거래 제어에서 운영 일시 중지 상태와 중지 사유를 확인합니다.",
    check_location_ko: "설정 > 실거래 제어 > 운영 일시 중지",
  },
  TRADING_PAUSED: {
    category: "operational_control",
    title_ko: "운영 일시 중지 상태입니다",
    detail_ko: "운영 중지 플래그가 켜져 있어 신규 주문을 보내지 않습니다.",
    auto_clear_hint_ko: "운영자가 resume하거나 자동 복구 정책이 통과해야 해소됩니다.",
    operator_action_ko: "trading_paused, pause reason, auto resume blockers를 확인합니다.",
    check_location_ko: "설정 > 실거래 제어 / 승인 운영 요약",
  },
  LIVE_APPROVAL_REQUIRED: {
    category: "operational_control",
    title_ko: "실거래 승인 창이 닫혀 있습니다",
    detail_ko: "신규 진입 전에 실거래 승인 창을 다시 열어야 합니다.",
    auto_clear_hint_ko: "수동 승인 창을 열거나 정책상 승인 유예가 생기면 해소됩니다.",
    operator_action_ko: "실거래 승인 상태와 승인 만료 시각을 확인합니다.",
    check_location_ko: "설정 > 실거래 제어 > 실거래 승인 상태",
  },
  LIVE_APPROVAL_POLICY_DISABLED: {
    category: "operational_control",
    title_ko: "실거래 승인 정책이 비활성화되어 있습니다",
    detail_ko: "수동 승인 정책을 사용하지 않는 설정입니다.",
    auto_clear_hint_ko: "설정 변경 전까지 이 상태는 유지됩니다.",
    operator_action_ko: "실거래 승인 정책 설정을 확인합니다.",
    check_location_ko: "설정 > 실거래 제어",
  },
  LIVE_TRADING_DISABLED: {
    category: "operational_control",
    title_ko: "실거래 사용 설정이 꺼져 있습니다",
    detail_ko: "실거래 주문 제출 경로가 설정상 비활성화되어 있습니다.",
    auto_clear_hint_ko: "운영자가 설정을 바꾸기 전까지 자동 해소되지 않습니다.",
    operator_action_ko: "실거래 사용 설정과 rollout mode를 확인합니다.",
    check_location_ko: "설정 > 실거래 제어 > 운영 모드",
  },
  LIVE_ENV_DISABLED: {
    category: "operational_control",
    title_ko: "실거래 환경 플래그가 꺼져 있습니다",
    detail_ko: "환경 설정상 실거래 주문 제출을 사용할 수 없습니다.",
    auto_clear_hint_ko: "환경 설정 변경 전까지 자동 해소되지 않습니다.",
    operator_action_ko: "실거래 환경 변수와 운영 모드를 확인합니다.",
    check_location_ko: "설정 > 실거래 제어 / 배포 환경",
  },
  LIVE_TRADING_ENV_DISABLED: {
    category: "operational_control",
    title_ko: "실거래 환경 플래그가 꺼져 있습니다",
    detail_ko: "환경 설정상 실거래 주문 제출을 사용할 수 없습니다.",
    auto_clear_hint_ko: "환경 설정 변경 전까지 자동 해소되지 않습니다.",
    operator_action_ko: "실거래 환경 변수와 운영 모드를 확인합니다.",
    check_location_ko: "설정 > 실거래 제어 / 배포 환경",
  },
  LIVE_CREDENTIALS_MISSING: {
    category: "operational_control",
    title_ko: "실거래 API 자격 정보가 없습니다",
    detail_ko: "실거래 API Key 또는 Secret이 설정되지 않아 실제 주문 제출을 사용할 수 없습니다.",
    auto_clear_hint_ko: "자격 정보가 설정되고 서비스가 해당 설정을 읽으면 해소될 수 있습니다.",
    operator_action_ko: "실거래 API Key/Secret 설정과 배포 환경을 확인합니다.",
    check_location_ko: "설정 > 연동 설정 / 배포 환경",
  },
  ROLLOUT_MODE_SHADOW: {
    category: "operational_control",
    title_ko: "섀도 모드라 실제 주문을 보내지 않습니다",
    detail_ko: "판단과 기록만 수행하고 거래소 주문 제출은 금지됩니다.",
    auto_clear_hint_ko: "운영 모드를 변경하기 전까지 자동 해소되지 않습니다.",
    operator_action_ko: "rollout mode가 의도한 값인지 확인합니다.",
    check_location_ko: "설정 > 실거래 제어 > 운영 모드",
  },
  ROLLOUT_MODE_LIVE_DRY_RUN: {
    category: "operational_control",
    title_ko: "실거래 드라이런 모드라 실제 주문을 보내지 않습니다",
    detail_ko: "거래소 동기화와 사전 점검까지만 수행하고 주문 제출은 금지됩니다.",
    auto_clear_hint_ko: "운영 모드를 변경하기 전까지 자동 해소되지 않습니다.",
    operator_action_ko: "rollout mode가 의도한 값인지 확인합니다.",
    check_location_ko: "설정 > 실거래 제어 > 운영 모드",
  },
};

const safetyReasonDefinitions: Record<string, ReasonCodeDefinition> = {
  ACCOUNT_STATE_STALE: {
    category: "safety_block",
    title_ko: "계좌 정보가 오래되어 신규 진입을 막았습니다",
    detail_ko: "잔고 표시와 별도로 신규 진입용 계좌 동기화 기준을 다시 확인 중입니다.",
    auto_clear_hint_ko: "동기화가 정상으로 돌아오면 자동 해소될 수 있습니다.",
    operator_action_ko: "계좌 동기화 상태와 최신 갱신 시각을 확인합니다.",
    check_location_ko: "안전 점검 > 거래소 정보 최신성 > 계좌",
  },
  POSITION_STATE_STALE: {
    category: "safety_block",
    title_ko: "포지션 정보가 오래되어 신규 진입을 막았습니다",
    detail_ko: "신규 진입용 포지션 동기화 기준을 다시 확인 중입니다.",
    auto_clear_hint_ko: "포지션 동기화가 정상으로 돌아오면 자동 해소될 수 있습니다.",
    operator_action_ko: "포지션 동기화 상태와 최신 갱신 시각을 확인합니다.",
    check_location_ko: "안전 점검 > 거래소 정보 최신성 > 포지션",
  },
  OPEN_ORDERS_STATE_STALE: {
    category: "safety_block",
    title_ko: "열린 주문 정보가 오래되어 신규 진입을 막았습니다",
    detail_ko: "신규 진입용 미체결 주문 동기화 기준을 다시 확인 중입니다.",
    auto_clear_hint_ko: "열린 주문 동기화가 정상으로 돌아오면 자동 해소될 수 있습니다.",
    operator_action_ko: "미체결 주문 동기화 상태와 최신 갱신 시각을 확인합니다.",
    check_location_ko: "안전 점검 > 거래소 정보 최신성 > 오더",
  },
  PROTECTION_STATE_UNVERIFIED: {
    category: "safety_block",
    title_ko: "보호 주문 상태 확인이 끝나지 않았습니다",
    detail_ko: "보호 주문이 검증되기 전에는 신규 진입을 잠시 보류합니다.",
    auto_clear_hint_ko: "보호 주문 검증이 완료되면 자동 해소될 수 있습니다.",
    operator_action_ko: "보호 주문 상태와 미보호 포지션 수를 확인합니다.",
    check_location_ko: "안전 점검 > 보호 주문",
  },
  MARKET_STATE_STALE: {
    category: "safety_block",
    title_ko: "시장 데이터가 오래되어 신규 진입을 막았습니다",
    detail_ko: "시장 데이터 최신성이 부족해 현재 가격 기준 판단을 신뢰할 수 없습니다.",
    auto_clear_hint_ko: "시장 데이터가 다시 갱신되면 자동 해소될 수 있습니다.",
    operator_action_ko: "시장 데이터 source/status와 마지막 갱신 시각을 확인합니다.",
    check_location_ko: "안전 점검 > 시장 / market freshness",
  },
  MARKET_STATE_INCOMPLETE: {
    category: "safety_block",
    title_ko: "시장 데이터가 불완전합니다",
    detail_ko: "필요한 시장 지표가 부족해 신규 진입을 차단했습니다.",
    auto_clear_hint_ko: "누락된 시장 데이터가 채워지면 자동 해소될 수 있습니다.",
    operator_action_ko: "시장 데이터 source/status와 누락 항목을 확인합니다.",
    check_location_ko: "안전 점검 > 시장 / market freshness",
  },
  UNRESOLVED_SUBMISSION_GUARD_ACTIVE: {
    category: "safety_block",
    title_ko: "결과 미확인 주문이 있어 신규 진입을 막았습니다",
    detail_ko: "이전 주문 제출 결과가 명확하지 않아 중복 주문 위험을 막고 있습니다.",
    auto_clear_hint_ko: "reconciliation이 주문 결과를 확인하면 해소될 수 있습니다.",
    operator_action_ko: "submit_unknown 후보와 unresolved submission count를 확인합니다.",
    check_location_ko: "안전 점검 > 주문 제출 / reconciliation",
  },
  UNRESOLVED_SUBMISSION_DEADLINE_EXCEEDED: {
    category: "safety_block",
    title_ko: "미해결 주문 확인 시간이 초과되었습니다",
    detail_ko: "주문 제출 결과가 제한 시간 안에 확인되지 않아 신규 진입을 차단했습니다.",
    auto_clear_hint_ko: "운영자가 reconciliation 결과를 확인해야 합니다.",
    operator_action_ko: "unresolved submission과 거래소 주문 상태를 확인합니다.",
    check_location_ko: "안전 점검 > 주문 제출 / reconciliation",
  },
  LIVE_ORDER_SUBMISSION_UNKNOWN: {
    category: "safety_block",
    title_ko: "주문 제출 결과가 불명확합니다",
    detail_ko: "거래소 주문 제출 결과를 확정할 수 없어 신규 진입을 막고 있습니다.",
    auto_clear_hint_ko: "주문 결과가 거래소와 로컬 상태에서 일치하면 해소될 수 있습니다.",
    operator_action_ko: "submit_unknown 후보와 live order 상태를 확인합니다.",
    check_location_ko: "안전 점검 > 주문 제출 / reconciliation",
  },
  RECONCILIATION_DEGRADED: {
    category: "safety_block",
    title_ko: "거래소 reconciliation 상태가 저하되었습니다",
    detail_ko: "로컬 상태와 거래소 상태 확인이 완전하지 않아 신규 진입을 막을 수 있습니다.",
    auto_clear_hint_ko: "reconciliation이 synced로 돌아오면 자동 해소될 수 있습니다.",
    operator_action_ko: "reconciliation status와 blocked reason을 확인합니다.",
    check_location_ko: "안전 점검 > reconciliation",
  },
  RECONCILIATION_UNSYNCED: {
    category: "safety_block",
    title_ko: "거래소 reconciliation이 동기화되지 않았습니다",
    detail_ko: "로컬 상태와 거래소 상태가 맞지 않아 신규 진입을 막습니다.",
    auto_clear_hint_ko: "reconciliation이 synced로 돌아오면 자동 해소될 수 있습니다.",
    operator_action_ko: "reconciliation status와 blocked session을 확인합니다.",
    check_location_ko: "안전 점검 > reconciliation",
  },
  PROTECTION_REQUIRED: {
    category: "safety_block",
    title_ko: "보호 주문 복구가 필요합니다",
    detail_ko: "보호 주문 복구가 끝나기 전까지 신규 진입보다 보호 조치를 우선합니다.",
    auto_clear_hint_ko: "보호 주문이 복구되고 검증되면 자동 해소될 수 있습니다.",
    operator_action_ko: "미보호 포지션과 보호 주문 복구 상태를 확인합니다.",
    check_location_ko: "안전 점검 > 보호 주문",
  },
  DEGRADED_MANAGE_ONLY: {
    category: "safety_block",
    title_ko: "관리 전용 상태라 신규 진입을 막았습니다",
    detail_ko: "신규 진입보다 기존 포지션 보호와 정리를 우선하는 상태입니다.",
    auto_clear_hint_ko: "운영 상태가 정상으로 돌아오면 해소될 수 있습니다.",
    operator_action_ko: "operating_state와 protection recovery status를 확인합니다.",
    check_location_ko: "안전 점검 > 운영 상태 / 보호 주문",
  },
  EMERGENCY_EXIT: {
    category: "safety_block",
    title_ko: "비상 대응 상태라 신규 진입을 막았습니다",
    detail_ko: "비상 청산 또는 보호 대응 중에는 새 포지션을 열지 않습니다.",
    auto_clear_hint_ko: "비상 대응이 종료되고 안전 점검이 통과해야 해소됩니다.",
    operator_action_ko: "emergency action과 operating_state를 확인합니다.",
    check_location_ko: "안전 점검 > 운영 상태",
  },
  ACCOUNT_STATE_INCONSISTENT: {
    category: "safety_block",
    title_ko: "로컬 계좌 상태와 거래소 상태가 일치하지 않습니다",
    detail_ko: "상태 불일치가 있어 신규 진입을 차단했습니다.",
    auto_clear_hint_ko: "계좌 reconciliation이 synced로 돌아오면 해소될 수 있습니다.",
    operator_action_ko: "계좌 동기화와 reconciliation status를 확인합니다.",
    check_location_ko: "안전 점검 > 계좌 / reconciliation",
  },
  PORTFOLIO_RISK_UNCERTAIN: {
    category: "safety_block",
    title_ko: "포트폴리오 리스크 상태를 신뢰할 수 없습니다",
    detail_ko: "노출 또는 포지션 기준을 확정할 수 없어 신규 진입을 막습니다.",
    auto_clear_hint_ko: "포트폴리오 리스크 계산 입력이 정상화되면 해소될 수 있습니다.",
    operator_action_ko: "노출 요약과 동기화 상태를 확인합니다.",
    check_location_ko: "안전 점검 > 노출 / 포트폴리오 리스크",
  },
  BINANCE_REST_CIRCUIT_OPEN: {
    category: "safety_block",
    title_ko: "Binance REST 회로가 열려 상태 확인이 제한됩니다",
    detail_ko: "거래소 상태 확인이 불안정해 신규 진입을 보수적으로 막을 수 있습니다.",
    auto_clear_hint_ko: "거래소 REST 상태가 회복되고 동기화가 성공하면 해소될 수 있습니다.",
    operator_action_ko: "거래소 연결 상태와 cache/source status를 확인합니다.",
    check_location_ko: "안전 점검 > 거래소 연결 / market source",
  },
  SYMBOL_RECENT_PERFORMANCE_NEGATIVE: {
    category: "safety_block",
    title_ko: "최근 해당 심볼 실현손익이 수수료 차감 후 음수입니다",
    detail_ko: "최근 실행 이력이 수수료 차감 후 손실 구간이라 같은 심볼의 신규 진입을 막았습니다.",
    auto_clear_hint_ko: "최근 거래 성과가 순손익 기준으로 회복되면 다음 리스크 점검에서 자동 해소될 수 있습니다.",
    operator_action_ko: "리스크 카드의 차단 사유 근거에서 조회 기간, 실행 건수, 총손익/수수료/순손익을 확인하세요.",
    check_location_ko: "운영 판단 > 리스크 > 거래 안 된 이유 / 차단 사유",
  },
  DECISION_BUCKET_RECENT_PERFORMANCE_NEGATIVE: {
    category: "safety_block",
    title_ko: "최근 같은 판단 버킷의 순손익이 음수입니다",
    detail_ko: "심볼, 방향, 레짐이 같은 최근 체결 버킷의 수수료 차감 후 기대값이 음수라 신규 진입을 차단합니다.",
    auto_clear_hint_ko: "같은 버킷의 최근 성과가 충분한 표본에서 회복되면 다음 리스크 평가에서 자동 해소됩니다.",
    operator_action_ko: "리스크 카드의 판단 버킷 성과 근거에서 버킷, 표본 수, 순손익, 기대값을 확인하세요.",
    check_location_ko: "운영 진단 > 리스크 > 거래 안 된 이유 / 차단 사유",
  },
  CORRELATED_EXPOSURE_LIMIT_REACHED: {
    category: "safety_block",
    title_ko: "BTC/ETH 동일방향 상관 노출 한도를 초과했습니다",
    detail_ko: "신규 주문을 더하면 BTC/ETH 계열 동일방향 노출이 설정 한도를 넘기 때문에 주문 제출 전 차단했습니다.",
    auto_clear_hint_ko: "기존 노출이 줄거나 반대 방향 노출이 정리되어 한도 안으로 돌아오면 자동 해소될 수 있습니다.",
    operator_action_ko: "리스크 카드의 차단 사유 근거에서 동일방향 노출 비율과 한도 값을 확인하세요.",
    check_location_ko: "운영 판단 > 리스크 > 거래 안 된 이유 / 차단 사유",
  },
  SLIPPAGE_THRESHOLD_EXCEEDED: {
    category: "safety_block",
    title_ko: "주문 가격 괴리가 허용 범위를 넘었습니다",
    detail_ko: "주문 제출 직전 기준 가격과 현재 시장가 차이가 커서 주문을 보내지 않았습니다.",
    auto_clear_hint_ko: "가격 괴리가 허용 범위로 줄어들면 다음 판단 주기에서 다시 검토됩니다.",
    operator_action_ko: "기준 가격, 현재가, 허용 가격 괴리 기준을 확인합니다.",
    check_location_ko: "안전 점검 > 주문 가격 괴리 / 허용 가격 괴리",
  },
};

const positionExitReviewReasonDefinitions: Record<string, ReasonCodeDefinition> = {
  POSITION_EXIT_REVIEW_LOCAL_FILTER_BLOCKED: {
    category: "safety_block",
    title_ko: "AI 익절 제안이 로컬 보유전략 필터에서 차단됐습니다",
    detail_ko: "AI가 익절/축소 후보를 냈지만 현재 포지션의 초단기/스윙/장기 보유전략 조건과 맞지 않아 실행 후보로 쓰지 않았습니다.",
    auto_clear_hint_ko: "포지션의 R 배수, 최대 유리 이동폭 되돌림, 시간 조건, 레짐 약화 같은 규칙 기반 근거가 생기면 다음 관리 주기에서 다시 평가됩니다.",
    operator_action_ko: "AI 익절 판단의 recommendation과 position_management의 holding_profile, reduce_reason_codes, partial_take_profit_ready 값을 함께 확인하세요.",
    check_location_ko: "대시보드 > 포지션 > AI 익절 판단 / position_management payload",
  },
  POSITION_EXIT_REVIEW_PARTIAL_NOT_READY: {
    category: "safety_block",
    title_ko: "부분익절 조건이 아직 충족되지 않았습니다",
    detail_ko: "AI가 부분익절을 제안했지만 규칙 기반 부분익절 준비 상태가 false라서 실제 축소 전용 부분익절 후보로 쓰지 않았습니다.",
    auto_clear_hint_ko: "설정된 partial TP R 기준과 포지션 수익 조건이 충족되면 다음 관리 주기에서 자동으로 다시 평가됩니다.",
    operator_action_ko: "position_management.partial_take_profit_ready, current_r_multiple, partial_take_profit_trigger_r, partial_take_profit_taken 값을 확인하세요.",
    check_location_ko: "대시보드 > 포지션 > 포지션 관리 상태",
  },
  POSITION_EXIT_REVIEW_PARTIAL_TP_ALREADY_TAKEN: {
    category: "safety_block",
    title_ko: "이미 부분익절이 처리된 포지션입니다",
    detail_ko: "AI가 부분익절을 제안했지만 이 포지션은 이미 partial take-profit 처리 이력이 있어 중복 축소 후보로 쓰지 않았습니다.",
    auto_clear_hint_ko: "남은 수량은 잔여 수량 관리 또는 보호주문/트레일링 조건으로 계속 관리됩니다.",
    operator_action_ko: "포지션 관리 상태에서 부분익절 처리 여부와 남은 포지션 수량을 확인하세요.",
    check_location_ko: "대시보드 > 포지션 > 포지션 관리 상태",
  },
  POSITION_EXIT_REVIEW_SCALP_RUNNER_BLOCKED: {
    category: "safety_block",
    title_ko: "단타 포지션: 잔여 수량 축소 근거가 부족합니다",
    detail_ko: "단타 보유전략에서는 긴 잔여 수량 관리보다 빠른 실패/약화 근거가 필요합니다. 해당 근거가 없어 AI의 축소 또는 전량 익절 제안을 막았습니다.",
    auto_clear_hint_ko: "초단기 실패 준비, 시간 조건, 모멘텀 약화, 레짐 전환 같은 근거가 생기면 다시 평가됩니다.",
    operator_action_ko: "보유전략이 단타인지, 부분익절 이후 잔여 수량 관리 상태인지, 빠른 실패/약화 사유가 있는지 확인하세요.",
    check_location_ko: "대시보드 > 포지션 > AI 익절 판단 / 포지션 관리 상태",
  },
  POSITION_EXIT_REVIEW_SWING_RUNNER_SIGNAL_REQUIRED: {
    category: "safety_block",
    title_ko: "스윙 포지션: 잔여 수량 훼손 근거가 아직 부족합니다",
    detail_ko: "스윙 보유전략에서는 부분익절 이후 잔여 수량 훼손, 최대 유리 이동폭 되돌림, 축소 사유 코드 같은 근거가 있어야 AI 축소/청산 후보를 허용합니다.",
    auto_clear_hint_ko: "부분익절 이후 잔여 수량이 약해지거나 되돌림/축소 사유 코드가 생기면 다음 관리 주기에서 다시 검토됩니다.",
    operator_action_ko: "부분익절 처리 여부, 최대 유리 이동폭 되돌림, 축소 사유, 부분익절 이후 잔여 수량 상태를 확인하세요.",
    check_location_ko: "대시보드 > 포지션 > AI 익절 판단 / 포지션 관리 상태",
  },
  POSITION_EXIT_REVIEW_POSITION_EXIT_TOO_EARLY: {
    category: "safety_block",
    title_ko: "장기 보유 포지션: 전량 익절 근거가 아직 이릅니다",
    detail_ko: "장기 보유전략에서는 더 긴 보유와 보호적 손절 조정을 우선합니다. 로컬 청산 계열 사유 코드가 없어 AI 청산/축소 제안을 막았습니다.",
    auto_clear_hint_ko: "로컬 청산 사유 코드가 생기거나 보호적 추적 손절/본전 이동 조건이 충족되면 다음 관리 주기에서 다시 평가됩니다.",
    operator_action_ko: "holding_profile=position, reduce_reason_codes의 EXIT 계열 코드, tightened_stop_loss 후보를 확인하세요.",
    check_location_ko: "대시보드 > 포지션 > AI 익절 판단 / 포지션 관리 상태",
  },
  POSITION_EXIT_REVIEW_STALE_SYNC: {
    category: "safety_block",
    title_ko: "동기화 상태가 오래되어 AI 익절 실행 후보를 차단했습니다",
    detail_ko: "계정, 포지션, 미체결 주문, 보호주문 동기화 중 하나라도 신뢰하기 어려워 AI 익절 판단을 실행 후보로 사용하지 않았습니다.",
    auto_clear_hint_ko: "동기화가 정상 상태로 회복되면 다음 포지션 관리 주기에서 다시 평가됩니다.",
    operator_action_ko: "sync freshness summary에서 account, positions, open_orders, protective_orders 상태를 확인하세요.",
    check_location_ko: "운영 진단 > 거래소 동기화 / 포지션 관리 audit",
  },
  POSITION_EXIT_REVIEW_PROTECTION_UNVERIFIED: {
    category: "safety_block",
    title_ko: "보호주문 검증 전이라 AI 익절 실행 후보를 차단했습니다",
    detail_ko: "포지션의 보호주문 상태가 protected로 확인되지 않아 AI 익절/축소 판단을 실행 후보로 사용하지 않았습니다.",
    auto_clear_hint_ko: "보호주문 검증이 완료되면 다음 관리 주기에서 다시 평가됩니다.",
    operator_action_ko: "손절/익절 보호주문이 거래소에 축소 전용 또는 청산 전용으로 살아 있는지 확인하세요.",
    check_location_ko: "대시보드 > 포지션 > 보호주문 상태",
  },
  POSITION_EXIT_REVIEW_STOP_RELAXATION_IGNORED: {
    category: "safety_block",
    title_ko: "AI의 손절 완화 제안은 무시됐습니다",
    detail_ko: "AI 출력에 손절을 넓히거나 약화시키는 내용이 감지되어 적용하지 않고 감사 기록만 남겼습니다. 손절 권한은 규칙 기반 고정 손절에 남아 있습니다.",
    auto_clear_hint_ko: "AI가 더 보호적인 손절 조임을 제안하거나 규칙 기반 손절 조건이 충족되면 별도 보호 경로에서 평가됩니다.",
    operator_action_ko: "감사 상세의 손절 완화 내용과 현재 보호 손절 상태를 확인하세요.",
    check_location_ko: "감사 로그 > position_exit_review_stop_relaxation_ignored",
  },
  POSITION_EXIT_REVIEW_BREAKEVEN_NOT_MORE_PROTECTIVE: {
    category: "safety_block",
    title_ko: "본전 이동 후보가 현재 손절보다 보호적이지 않습니다",
    detail_ko: "AI가 본전 이동을 제안했지만 규칙 기반 손절 후보가 현재 손절보다 더 보호적이지 않아 적용하지 않았습니다.",
    auto_clear_hint_ko: "현재가와 손절 후보가 더 보호적인 구조가 되면 다음 관리 주기에서 다시 평가됩니다.",
    operator_action_ko: "break_even_stop_loss, current_stop_loss, mark_price를 확인하세요.",
    check_location_ko: "대시보드 > 포지션 > 포지션 관리 상태",
  },
  POSITION_EXIT_REVIEW_TIGHTEN_NOT_MORE_PROTECTIVE: {
    category: "safety_block",
    title_ko: "트레일링 조임 후보가 현재 손절보다 보호적이지 않습니다",
    detail_ko: "AI가 추적 손절 조임을 제안했지만 규칙 기반 손절 후보가 현재 손절보다 더 보호적이지 않아 적용하지 않았습니다.",
    auto_clear_hint_ko: "ATR 추적 손절 또는 최대 유리 이동폭 되돌림 손절 후보가 현재 손절보다 보호적으로 계산되면 다시 평가됩니다.",
    operator_action_ko: "tightened_stop_loss, current_stop_loss, atr_trailing_stop_enabled, mfe_rollback 상태를 확인하세요.",
    check_location_ko: "대시보드 > 포지션 > 포지션 관리 상태",
  },
  POSITION_EXIT_REVIEW_FULL_TAKE_PROFIT: {
    category: "safety_block",
    title_ko: "AI 전량 익절 후보",
    detail_ko: "AI가 전량 익절을 제안했고 로컬 필터와 리스크/실행 게이트를 통과할 때만 청산 전용 후보로 사용됩니다.",
    auto_clear_hint_ko: "후보 상태는 다음 포지션 관리 주기에서 새 데이터로 다시 평가됩니다.",
    operator_action_ko: "position_exit_review_execution과 risk_result, execution_result를 함께 확인하세요.",
    check_location_ko: "대시보드 > 포지션 > AI 익절 판단",
  },
  POSITION_EXIT_REVIEW_PARTIAL_TAKE_PROFIT: {
    category: "safety_block",
    title_ko: "AI 부분익절 후보",
    detail_ko: "AI가 부분익절을 제안했고 규칙 기반 부분익절 준비 상태가 true일 때만 축소 전용 후보로 사용됩니다.",
    auto_clear_hint_ko: "partial TP 조건은 다음 포지션 관리 주기에서 새 R 배수 기준으로 다시 평가됩니다.",
    operator_action_ko: "partial_take_profit_ready, partial_take_profit_fraction, current_r_multiple을 확인하세요.",
    check_location_ko: "대시보드 > 포지션 > AI 익절 판단",
  },
  POSITION_EXIT_REVIEW_REDUCE_RISK_ONLY: {
    category: "safety_block",
    title_ko: "AI 리스크 축소 후보",
    detail_ko: "AI가 포지션 축소를 제안했고 로컬 보유전략 필터와 리스크 게이트를 통과할 때만 축소 전용 후보로 사용됩니다.",
    auto_clear_hint_ko: "잔여 수량 훼손 또는 시간/레짐 약화 근거가 바뀌면 다음 관리 주기에서 다시 평가됩니다.",
    operator_action_ko: "holding_profile, reduce_reason_codes, mfe_rollback_triggered, time_to_fail_ready를 확인하세요.",
    check_location_ko: "대시보드 > 포지션 > AI 익절 판단",
  },
};

const compatibilityDetailMap: Record<string, string> = {
  ENTRY_AUTO_RESIZED: "신규 진입 크기를 자동으로 줄였습니다.",
  ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT: "전체 노출 한도에 맞춰 신규 진입 크기를 줄였습니다.",
  ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT: "한쪽 방향 쏠림을 줄이기 위해 신규 진입 크기를 줄였습니다.",
  ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT: "단일 포지션 한도에 맞춰 신규 진입 크기를 줄였습니다.",
  ENTRY_CLAMPED_TO_SAME_TIER_LIMIT: "같은 티어 집중도를 낮추기 위해 신규 진입 크기를 줄였습니다.",
  ENTRY_SIZE_BELOW_MIN_NOTIONAL: "거래소 최소 주문 금액보다 작아 신규 진입을 차단했습니다.",
  INVALID_LONG_BRACKETS: "롱 포지션의 손절/익절 가격 구조가 유효하지 않습니다.",
  INVALID_SHORT_BRACKETS: "숏 포지션의 손절/익절 가격 구조가 유효하지 않습니다.",
  INVALID_PROTECTION_BRACKETS: "보호 복구용 손절/익절 가격 구조가 유효하지 않습니다.",
  INVALID_INVALIDATION_PRICE: "무효화 가격 기준이 맞지 않습니다.",
  DAILY_LOSS_LIMIT_REACHED: "일일 손실 한도에 도달해 신규 진입을 차단했습니다.",
  MAX_CONSECUTIVE_LOSSES_REACHED: "연속 손실 한도에 도달해 보수적 제한이 적용되었습니다.",
  GROSS_EXPOSURE_LIMIT_REACHED: "총 노출 한도를 초과해 신규 진입을 차단했습니다.",
  LARGEST_POSITION_LIMIT_REACHED: "요청 수량이 단일 심볼 한도를 초과했습니다.",
  DIRECTIONAL_BIAS_LIMIT_REACHED: "방향 편향 한도를 초과해 신규 진입을 차단했습니다.",
  SAME_TIER_CONCENTRATION_LIMIT_REACHED: "동일 티어 집중도 한도를 초과해 신규 진입을 차단했습니다.",
  PLAN_CANCELED_NO_ENTRY_CAPACITY: "이미 열린 포지션 때문에 추가 진입 여유가 없어 대기 플랜 감시를 중단했습니다.",
  REPLACED_BY_NEW_APPROVED_PLAN: "더 최신 승인 플랜으로 대체되어 이전 대기 플랜 감시를 중단했습니다.",
  EXCHANGE_ACCOUNT_STATE_UNAVAILABLE: "거래소 계좌 상태를 확인할 수 없습니다.",
  EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE: "거래소 또는 네트워크 연결이 일시적으로 불안정합니다.",
  TEMPORARY_MARKET_DATA_FAILURE: "시장 데이터 확인 중 일시적인 오류가 발생했습니다.",
  TEMPORARY_SYNC_FAILURE: "거래소 상태 동기화 중 일시적인 오류가 발생했습니다.",
  EXCHANGE_POSITION_SYNC_FAILED: "거래소 포지션 상태를 동기화하지 못했습니다.",
  EXCHANGE_OPEN_ORDERS_SYNC_FAILED: "거래소 미체결 주문 상태를 동기화하지 못했습니다.",
  BINANCE_REST_RECOVERING_SYNC_STALE: "Binance REST 복구 중이며 동기화 상태가 아직 오래되었습니다.",
  BINANCE_REST_RECOVERING_SYNC_REQUIRED: "Binance REST 복구 후 거래소 상태 재동기화가 필요합니다.",
  BINANCE_REST_TRANSPORT_ERROR: "Binance REST 전송 오류가 감지되었습니다.",
  BINANCE_REST_SERVER_ERROR: "Binance REST 서버 오류가 감지되었습니다.",
  BINANCE_REST_RATE_LIMITED: "Binance REST rate limit으로 요청이 제한되었습니다.",
  BINANCE_REST_MUTATING_ORDER_FAILED: "Binance REST 주문 변경 요청이 실패했습니다.",
  DRAWDOWN_STATE_CAUTION: "손실/드로다운 주의 상태라 진입 크기를 보수적으로 제한합니다.",
  DRAWDOWN_STATE_CONTAINMENT: "드로다운 억제 상태라 신규 진입을 더 강하게 제한합니다.",
  DRAWDOWN_STATE_RECOVERY: "회복 상태라 신규 진입 리스크를 낮춰 운용합니다.",
};

const reasonDefinitions: Record<string, ReasonCodeDefinition> = {
  ...entryWaitReasonDefinitions,
  ...operationalReasonDefinitions,
  ...safetyReasonDefinitions,
  ...positionExitReviewReasonDefinitions,
};

const operatorReasonOverrides: Record<string, ReasonCodeDefinition> = {
  SYMBOL_RECENT_PERFORMANCE_NEGATIVE: {
    category: "safety_block",
    title_ko: "최근 BTCUSDT 실거래 성과가 수수료 차감 후 손실입니다",
    detail_ko: "최근 BTCUSDT 체결 이력의 총손익에서 수수료를 뺀 순손익이 음수라서 같은 심볼의 신규 진입을 보류했습니다.",
    auto_clear_hint_ko: "최근 BTCUSDT 실거래 성과가 충분한 표본에서 수수료 차감 후 순손익 기준으로 회복되면 다음 리스크 평가에서 자동 해제될 수 있습니다.",
    operator_action_ko: "리스크 근거에서 조회 기간, 실행 건수, 총손익, 수수료, 수수료 차감 후 순손익을 확인하세요.",
    check_location_ko: "운영 대시보드 > 리스크 > 차단 사유 근거",
  },
  DECISION_BUCKET_RECENT_PERFORMANCE_NEGATIVE: {
    category: "safety_block",
    title_ko: "BTCUSDT long 전환장 버킷의 최근 기대값이 음수입니다",
    detail_ko: "같은 심볼, 같은 방향, 같은 시장 레짐의 최근 체결 버킷에서 수수료 차감 후 순손익과 체결당 기대값이 모두 음수라서 신규 진입을 차단했습니다.",
    auto_clear_hint_ko: "같은 버킷의 최근 성과가 충분한 표본에서 수수료 차감 후 순손익과 기대값 기준으로 회복되면 다음 리스크 평가에서 자동 해제될 수 있습니다.",
    operator_action_ko: "판단 버킷 성과 근거에서 버킷 키, 체결 수, 판단 수, 수수료 차감 후 순손익, 수수료 차감 후 기대값을 확인하세요.",
    check_location_ko: "운영 대시보드 > 리스크 > 버킷 성과 근거",
  },
  CORRELATED_EXPOSURE_LIMIT_REACHED: {
    category: "safety_block",
    title_ko: "BTC/ETH 같은 방향 노출 한도를 넘습니다",
    detail_ko: "이 후보 주문을 더하면 BTC/ETH 계열의 같은 방향 노출이 허용 한도를 초과합니다. 그래서 실제 주문 제출 전에 차단했습니다.",
    auto_clear_hint_ko: "후보 주문 크기가 줄거나 기존 BTC/ETH 같은 방향 노출이 감소해 한도 안으로 돌아오면 다음 리스크 평가에서 자동 해제될 수 있습니다.",
    operator_action_ko: "portfolio_exposure_gate에서 candidate_notional_exposure, combined_BTC_ETH_directional_exposure_pct, limits.max_same_direction_major_exposure_pct를 확인하세요.",
    check_location_ko: "운영 대시보드 > 리스크 > 포트폴리오 노출 근거",
  },
};

const slippageEntryWaitContextCodes = new Set([
  "CHASE_LIMIT_EXCEEDED",
  "ENTRY_TRIGGER_NOT_MET",
  "PLAN_MAX_CHASE_EXCEEDED",
  "PLAN_LATE_CHASE_WAITING_REENTRY",
]);

const btcLongRecentPerformanceExposureBlockCodes = [
  "SYMBOL_RECENT_PERFORMANCE_NEGATIVE",
  "DECISION_BUCKET_RECENT_PERFORMANCE_NEGATIVE",
  "CORRELATED_EXPOSURE_LIMIT_REACHED",
];

const btcLongRecentPerformanceExposureBlockDefinition: ReasonCodeDefinition = {
  category: "safety_block",
  title_ko: "BTCUSDT long 후보는 AI 승인 주문이 아니라 리스크 평가에서 차단된 관찰 후보입니다",
  detail_ko: "AI 최종 판단은 HOLD였고, 별도로 감시 중이던 BTCUSDT long 후보가 진입 조건을 충족해 리스크 평가를 받았습니다. 최근 BTCUSDT 및 BTCUSDT long 전환장 성과가 수수료 차감 후 손실이고, 후보 주문 크기가 BTC/ETH 같은 방향 노출 한도를 넘어 실제 주문 제출 전에 차단됐습니다.",
  auto_clear_hint_ko: "성과 버킷의 수수료 차감 후 순손익이 회복되고 후보 크기 또는 BTC/ETH 같은 방향 노출이 한도 안으로 돌아오면 다음 리스크 평가에서 자동 해제될 수 있습니다.",
  operator_action_ko: "AgentRun 최종 decision, risk_check debug_payload의 symbol_recent_performance_gate, decision_bucket_recent_performance_gate, portfolio_exposure_gate를 함께 확인하세요.",
  check_location_ko: "운영 대시보드 > 리스크 > 차단 사유 근거",
};

const slippageEntryWaitDefinition: ReasonCodeDefinition = {
  category: "entry_wait",
  title_ko: "진입 가격대에서 벗어나 주문을 보류했습니다",
  detail_ko: "계획한 진입가와 현재가의 차이가 허용 범위를 넘었습니다. 지금 주문하면 계획보다 불리한 가격이 될 수 있어 이번 후보 주문을 보류합니다.",
  auto_clear_hint_ko: "가격이 진입 허용 범위로 돌아오면 다음 판단 주기에서 자동 재검토됩니다.",
  operator_action_ko: "진입 구간, 현재가, 허용 가격 괴리를 확인합니다.",
  check_location_ko: "진입 대기 계획 > 진입 구간 / 현재가 / 허용 가격 괴리",
};

function normalizeReasonCode(value: string | null | undefined) {
  return value?.trim().toUpperCase() ?? "";
}

function normalizeReasonCodeList(values: string[] | null | undefined) {
  return (values ?? []).map(normalizeReasonCode).filter((value) => value.length > 0);
}

function hasSlippageEntryWaitContext(values: string[] | null | undefined) {
  return normalizeReasonCodeList(values).some((value) => slippageEntryWaitContextCodes.has(value));
}

function hasBtcLongRecentPerformanceExposureBlockContext(values: string[] | null | undefined) {
  const codes = new Set(normalizeReasonCodeList(values));
  return btcLongRecentPerformanceExposureBlockCodes.every((value) => codes.has(value));
}

function inferCategory(code: string): ReasonCodeCategory {
  if (
    code.includes("APPROVAL") ||
    code.includes("LIVE_ARM") ||
    code.includes("LIVE_DISARM") ||
    code.includes("LIVE_TRADING") ||
    code.includes("ROLLOUT") ||
    code.includes("PAUSED")
  ) {
    return "operational_control";
  }
  if (
    code.includes("STALE") ||
    code.includes("INCOMPLETE") ||
    code.includes("UNTRUSTED") ||
    code.includes("PROTECTION") ||
    code.includes("PROTECTIVE") ||
    code.includes("RECONCILIATION") ||
    code.includes("SUBMISSION") ||
    code.includes("SYNC") ||
    code.includes("EXPOSURE") ||
    code.includes("NOTIONAL") ||
    code.includes("SLIPPAGE") ||
    code.includes("LOSS") ||
    code.includes("LIMIT") ||
    code.includes("EMERGENCY") ||
    code.includes("DEGRADED")
  ) {
    return "safety_block";
  }
  return "unknown";
}

function fallbackHint(category: ReasonCodeCategory) {
  if (category === "operational_control") {
    return {
      auto_clear_hint_ko: "운영 설정이나 승인 상태가 바뀌어야 해소됩니다.",
      operator_action_ko: "설정 > 실거래 제어에서 승인/운영 상태를 확인합니다.",
      check_location_ko: "설정 > 실거래 제어",
    };
  }
  if (category === "entry_wait") {
    return {
      auto_clear_hint_ko: "다음 판단 주기에서 조건이 맞으면 자동 재검토됩니다.",
      operator_action_ko: "진입 대기 계획과 이번 판단 주기 상태를 확인합니다.",
      check_location_ko: "진입 대기 계획 / 이번 판단 주기 상태",
    };
  }
  if (category === "safety_block") {
    return {
      auto_clear_hint_ko: "안전 점검 상태가 정상으로 돌아오면 자동 해소될 수 있습니다.",
      operator_action_ko: "안전 점검 화면에서 동기화, 주문, 보호 상태를 확인합니다.",
      check_location_ko: "안전 점검",
    };
  }
  return {
    auto_clear_hint_ko: "자동 해소 여부가 아직 매핑되지 않았습니다.",
    operator_action_ko: "원본 코드와 payload를 확인해 사용자용 설명 매핑이 필요한지 판단합니다.",
    check_location_ko: "고급 정보 / 원본 payload",
  };
}

export function describeReasonCode(value: string | null | undefined): ReasonCodeDisplay {
  const raw = value?.trim() ?? "";
  const code = normalizeReasonCode(value);
  if (!code) {
    return {
      raw_code: "",
      category: "unknown",
      title_ko: "추가 사유 없음",
      detail_ko: "표시할 reason code가 없습니다.",
      auto_clear_hint_ko: "추가 조치가 필요하지 않습니다.",
      operator_action_ko: "현재 화면의 운영 상태를 기준으로 확인합니다.",
      check_location_ko: "현재 화면",
      known: true,
    };
  }

  const definition = operatorReasonOverrides[code] ?? reasonDefinitions[code];
  if (definition) {
    return {
      raw_code: raw || code,
      known: true,
      ...definition,
    };
  }

  const compatibilityDetail = compatibilityDetailMap[code];
  const category = inferCategory(code);
  const hints = fallbackHint(category);
  if (compatibilityDetail) {
    return {
      raw_code: raw || code,
      category,
      title_ko: compatibilityDetail,
      detail_ko: compatibilityDetail,
      known: true,
      ...hints,
    };
  }

  return {
    raw_code: raw || code,
    category: "unknown",
    title_ko: "추가 확인이 필요한 사유입니다",
    detail_ko: `아직 사용자용 설명 매핑이 없는 내부 코드입니다. 원본 코드: ${raw || code}`,
    known: false,
    ...fallbackHint("unknown"),
  };
}

export function describeReasonCodeInContext(
  value: string | null | undefined,
  allReasonCodes: string[] | null | undefined,
): ReasonCodeDisplay {
  const raw = value?.trim() ?? "";
  const code = normalizeReasonCode(value);
  if (
    code === "SYMBOL_RECENT_PERFORMANCE_NEGATIVE" &&
    hasBtcLongRecentPerformanceExposureBlockContext(allReasonCodes)
  ) {
    return {
      raw_code: raw || code,
      known: true,
      ...btcLongRecentPerformanceExposureBlockDefinition,
    };
  }
  if (code === "SLIPPAGE_THRESHOLD_EXCEEDED" && hasSlippageEntryWaitContext(allReasonCodes)) {
    return {
      raw_code: raw || code,
      known: true,
      ...slippageEntryWaitDefinition,
    };
  }
  return describeReasonCode(value);
}

export function lookupRiskReasonCode(value: string | null | undefined) {
  const description = describeReasonCode(value);
  return description.known && description.raw_code ? description.detail_ko : null;
}

export function describeRiskReasonCode(value: string | null | undefined, emptyFallback = "추가 사유 없음") {
  if (!value || value.trim().length === 0) {
    return emptyFallback;
  }
  return describeReasonCode(value).detail_ko;
}

export function describeRiskReasonCodeInContext(
  value: string | null | undefined,
  allReasonCodes: string[] | null | undefined,
  emptyFallback = "추가 사유 없음",
) {
  if (!value || value.trim().length === 0) {
    return emptyFallback;
  }
  return describeReasonCodeInContext(value, allReasonCodes).detail_ko;
}

export function reasonCodeTitle(value: string | null | undefined) {
  return describeReasonCode(value).title_ko;
}

export function reasonCodeTitleInContext(value: string | null | undefined, allReasonCodes: string[] | null | undefined) {
  return describeReasonCodeInContext(value, allReasonCodes).title_ko;
}

export function reasonCodeDetail(value: string | null | undefined) {
  return describeReasonCode(value).detail_ko;
}

export function isEntryWaitReasonCode(value: string | null | undefined) {
  return describeReasonCode(value).category === "entry_wait";
}

export function isEntryWaitReasonCodeInContext(
  value: string | null | undefined,
  allReasonCodes: string[] | null | undefined,
) {
  return describeReasonCodeInContext(value, allReasonCodes).category === "entry_wait";
}

export function isOperationalControlReasonCode(value: string | null | undefined) {
  return describeReasonCode(value).category === "operational_control";
}

export function isSafetyBlockReasonCode(value: string | null | undefined) {
  return describeReasonCode(value).category === "safety_block";
}

export function filterNonEntryWaitReasonCodes(values: string[] | null | undefined) {
  return (values ?? []).filter((value) => value.trim().length > 0 && !isEntryWaitReasonCode(value));
}

export function filterNonEntryWaitReasonCodesInContext(
  values: string[] | null | undefined,
  allReasonCodes: string[] | null | undefined = values,
) {
  return (values ?? []).filter(
    (value) => value.trim().length > 0 && !isEntryWaitReasonCodeInContext(value, allReasonCodes),
  );
}

export function splitReasonCodesByCategory(values: string[] | null | undefined) {
  const result: Record<ReasonCodeCategory, string[]> = {
    operational_control: [],
    entry_wait: [],
    safety_block: [],
    unknown: [],
  };
  const seen = new Set<string>();
  for (const value of values ?? []) {
    const code = value.trim();
    if (!code || seen.has(code)) {
      continue;
    }
    seen.add(code);
    result[describeReasonCodeInContext(code, values).category].push(code);
  }
  return result;
}
