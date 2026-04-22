const sharedRiskReasonMap: Record<string, string> = {
  TRADING_PAUSED: "거래가 일시 중지되어 신규 진입을 차단했습니다.",
  LIVE_APPROVAL_REQUIRED: "실거래 승인 창이 닫혀 있어 신규 진입 전에 수동 승인이 필요합니다.",
  LIVE_APPROVAL_POLICY_DISABLED: "실거래 승인 정책이 비활성화되어 있습니다.",
  ENTRY_TRIGGER_NOT_MET: "현재 진입 트리거 조건이 충족되지 않았습니다.",
  ACCOUNT_STATE_STALE: "거래소 계좌 상태 동기화가 오래되어 신규 진입을 차단했습니다.",
  POSITION_STATE_STALE: "거래소 포지션 상태 동기화가 오래되어 신규 진입을 차단했습니다.",
  OPEN_ORDERS_STATE_STALE: "거래소 오더 상태 동기화가 오래되어 신규 진입을 차단했습니다.",
  PROTECTION_STATE_UNVERIFIED: "보호주문 상태를 확인할 수 없어 신규 진입을 차단했습니다.",
  MARKET_STATE_STALE: "시장 데이터가 오래되어 신규 진입을 차단했습니다.",
  MARKET_STATE_INCOMPLETE: "시장 데이터가 불완전하여 신규 진입을 차단했습니다.",
  ENTRY_SIZE_BELOW_MIN_NOTIONAL: "거래소 최소 주문 금액보다 작아 신규 진입을 차단했습니다.",
  INVALID_LONG_BRACKETS: "롱 포지션의 손절/익절 가격 구조가 유효하지 않습니다.",
  INVALID_SHORT_BRACKETS: "숏 포지션의 손절/익절 가격 구조가 유효하지 않습니다.",
  INVALID_PROTECTION_BRACKETS: "보호 복구용 손절/익절 가격 구조가 유효하지 않습니다.",
  SLIPPAGE_THRESHOLD_EXCEEDED: "허용한 슬리피지 범위를 넘어 신규 진입을 차단했습니다.",
  DAILY_LOSS_LIMIT_REACHED: "일일 손실 한도에 도달해 신규 진입을 차단했습니다.",
  MAX_CONSECUTIVE_LOSSES_REACHED: "연속 손실 한도에 도달해 보수적 제한이 적용되었습니다.",
  HOLD_DECISION: "현재 AI 판단은 신규 진입이 아닌 HOLD입니다.",
  GROSS_EXPOSURE_LIMIT_REACHED: "총 노출 한도를 초과해 신규 진입을 차단했습니다.",
  LARGEST_POSITION_LIMIT_REACHED: "심볼 집중도 한도 유지",
  DETERMINISTIC_BASELINE_DISAGREEMENT: "결정론적 기준선 불일치 상태 유지",
  DIRECTIONAL_BIAS_LIMIT_REACHED: "방향 편향 한도를 초과해 신규 진입을 차단했습니다.",
  SAME_TIER_CONCENTRATION_LIMIT_REACHED: "동일 티어 집중도 한도를 초과해 신규 진입을 차단했습니다.",
  PORTFOLIO_RISK_UNCERTAIN: "포트폴리오 리스크 상태를 신뢰할 수 없어 신규 진입을 차단했습니다.",
  ACCOUNT_STATE_INCONSISTENT: "로컬 상태와 거래소 상태가 일치하지 않아 신규 진입을 차단했습니다.",
  PROTECTION_REQUIRED: "보호주문 복구가 필요해 신규 진입보다 보호 조치를 우선합니다.",
  DEGRADED_MANAGE_ONLY: "운영 상태가 관리 전용으로 저하되어 신규 진입을 차단했습니다.",
  EMERGENCY_EXIT: "비상 청산 상태가 진행 중이라 신규 진입을 차단했습니다.",
  LIVE_ENV_DISABLED: "실거래 환경 플래그가 꺼져 있습니다.",
  LIVE_TRADING_DISABLED: "실거래 사용 설정이 꺼져 있습니다.",
  ROLLOUT_MODE_SHADOW: "shadow rollout 모드에서는 실제 거래소 주문 제출이 금지됩니다.",
  ROLLOUT_MODE_LIVE_DRY_RUN: "live dry-run rollout 모드에서는 실제 거래소 주문 제출이 금지됩니다.",
  LIVE_CREDENTIALS_MISSING: "실거래 API Key 또는 Secret이 설정되지 않았습니다.",
  EXCHANGE_ACCOUNT_STATE_UNAVAILABLE: "거래소 계좌 상태를 확인할 수 없습니다.",
  EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE: "거래소 또는 네트워크 연결이 일시적으로 불안정합니다.",
  TEMPORARY_MARKET_DATA_FAILURE: "시장 데이터 확인 중 일시적인 오류가 발생했습니다.",
  TEMPORARY_SYNC_FAILURE: "거래소 상태 동기화 중 일시적인 오류가 발생했습니다.",
  EXCHANGE_POSITION_SYNC_FAILED: "거래소 포지션 상태를 동기화하지 못했습니다.",
  EXCHANGE_OPEN_ORDERS_SYNC_FAILED: "거래소 미체결 주문 상태를 동기화하지 못했습니다.",
};

export function lookupRiskReasonCode(value: string | null | undefined) {
  if (!value || value.trim().length === 0) {
    return null;
  }
  return sharedRiskReasonMap[value] ?? null;
}

export function describeRiskReasonCode(value: string | null | undefined, emptyFallback = "추가 사유 없음") {
  return lookupRiskReasonCode(value) ?? (value && value.trim().length > 0 ? value : emptyFallback);
}
