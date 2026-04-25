# 리스크 정책

현재 하드 리스크 정책의 구현 기준은 [C:\my-trading-bot\backend\trading_mvp\services\risk.py](C:/my-trading-bot/backend/trading_mvp/services/risk.py)입니다.
이 문서는 실거래 신규 진입을 언제 차단하고, 언제 생존 경로를 우선 허용하는지 운영 기준만 간단히 정리합니다.

## 하드 리스크 기준

- BTC 최대 레버리지: `5x`
- 메이저 알트 최대 레버리지: `3x`
- 일반 알트 최대 레버리지: `2x`
- 1회 거래 최대 손실: equity 기준 `2%`
- 일일 손실 한도: equity 기준 `5%`
- 연속 손실 `3회` 이후 보수적 제한 적용

아래 상태에서는 신규 진입을 차단합니다.

- account / positions / open orders / protection 상태가 stale 또는 incomplete인 경우
- pause, degraded, approval 미충족 상태인 경우
- `operating_state`가 `PROTECTION_REQUIRED`, `DEGRADED_MANAGE_ONLY`, `EMERGENCY_EXIT`, `PAUSED`인 경우
- unresolved submission guard가 남아 있어 직전 주문 제출 결과가 아직 거래소와 대조되지 않은 경우
- 계좌, 시장, 포지션 상태를 신뢰할 수 없는 경우
- 보호주문 상태를 검증할 수 없는 경우

## 익스포저 하드 게이트

신규 진입과 추가 진입은 아래 익스포저 한도를 넘지 않아야 합니다.

- `max_gross_exposure_pct`
  - 전체 포지션 notional / equity
- `max_largest_position_pct`
  - 단일 심볼 포지션 notional / equity
- `max_directional_bias_pct`
  - long 또는 short 한쪽 방향 노출 / equity
- `max_same_tier_concentration_pct`
  - 동일 리스크 tier 포지션 합산 / equity

현재 기본 상한:

- 총 익스포저: `3.0`
- 최대 단일 포지션: `1.5`
- 방향 편중: `2.0`
- 동일 tier 집중도: `2.5`

## 노출도 초과 시 자동 축소 진입

익스포저 한도 초과가 유일한 문제라면, risk guard는 무조건 차단하지 않고 먼저 허용 가능한 headroom을 계산합니다.

- `gross exposure headroom`
- `directional headroom`
- `single position headroom`
- `same tier headroom`

이 값들 중 가장 작은 여유를 신규 진입 상한으로 사용합니다.

- raw projected size가 headroom보다 크면 `approved_projected_notional`로 clamp합니다.
- clamp 이후 주문 크기가 최소 실행 가능 주문 이상이면 `allowed=true`로 승인할 수 있습니다.
- 이 경우 `reason_codes`에는 차단 코드 대신 아래 정보 코드가 남습니다.
  - `ENTRY_AUTO_RESIZED`
  - `ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT`
  - `ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT`
  - `ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT`
  - `ENTRY_CLAMPED_TO_SAME_TIER_LIMIT`

아래 경우에는 자동 축소를 적용하지 않고 기존처럼 차단합니다.

- stale / incomplete / sync freshness 문제
- approval missing / live disabled
- leverage hard cap 초과
- risk_pct hard cap 초과
- stop / target 누락
- protection / degraded / emergency 관련 상태
- headroom이 `0` 이하인 경우
- headroom이 최소 실행 가능 주문 미만인 경우

## 주요 reason code

익스포저 차단과 자동 축소 관련 reason code:

- `GROSS_EXPOSURE_LIMIT_REACHED`
- `LARGEST_POSITION_LIMIT_REACHED`
- `DIRECTIONAL_BIAS_LIMIT_REACHED`
- `SAME_TIER_CONCENTRATION_LIMIT_REACHED`
- `ENTRY_AUTO_RESIZED`
- `ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT`
- `ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT`
- `ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT`
- `ENTRY_CLAMPED_TO_SAME_TIER_LIMIT`
- `ENTRY_SIZE_BELOW_MIN_NOTIONAL`

stale / incomplete / protection 검증 실패 관련 reason code:

- `ACCOUNT_STATE_STALE`
- `POSITION_STATE_STALE`
- `OPEN_ORDERS_STATE_STALE`
- `PROTECTION_STATE_UNVERIFIED`
- `MARKET_STATE_STALE`
- `MARKET_STATE_INCOMPLETE`
- `PROTECTION_REQUIRED`
- `DEGRADED_MANAGE_ONLY`
- `EMERGENCY_EXIT`
- `UNRESOLVED_SUBMISSION_GUARD_ACTIVE`
- `PROTECTION_VERIFY_FAILED`
- `EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE`
- `UNDERPERFORMING_SETUP_DISABLED`
  - 최근 실거래 bucket 성과가 충분히 나빠 adaptive setup disable가 active일 때 신규 진입만 차단합니다.
  - `reduce`, `exit`, `reduce_only`, `protection recovery`, `emergency_exit`는 이 code 때문에 막지 않습니다.

## 허용되는 생존 경로

신규 진입이 차단되더라도 아래 경로는 별도로 검토할 수 있습니다.

- `reduce`
- `exit`
- `reduce_only`
- 보호주문 복구
- `emergency_exit`

단, 보호주문 복구는 단순히 `intent_family=protection` 또는 `management_action=restore_protection`가 붙었다고 자동 허용되지 않습니다. 실제 코드 기준으로는 열린 포지션이 있고, 현재 운영 상태가 `PROTECTION_REQUIRED` 또는 `DEGRADED_MANAGE_ONLY`이며, 같은 방향의 `long` / `short` 복구 의도와 `stop_loss` / `take_profit`이 함께 있을 때만 survival path로 취급합니다. 이 조건을 만족하지 못한 보호 복구 형태의 `long` / `short`는 신규 진입처럼 freshness / protection / market blocker를 그대로 받습니다.

## Binance API 장애와 상태 전이

- Binance 계정, 포지션, 오픈오더, 보호주문 조회가 실패하면 해당 sync scope가 `failed` 또는 `incomplete`로 기록되고 신규 진입은 stale / incomplete reason code로 차단됩니다.
- timeout, transport error, 일부 일시적 Binance API code는 `EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE`로 분류됩니다.
- 주문 제출 timeout처럼 제출 여부를 확정할 수 없는 경우 execution은 `LIVE_ORDER_SUBMISSION_UNKNOWN`을 남기고 unresolved submission guard를 세웁니다. guard가 남아 있으면 다음 같은 symbol / direction 신규 진입은 거래소 호출 전에 `UNRESOLVED_SUBMISSION_GUARD_ACTIVE`로 차단됩니다.
- protective order 생성 후 검증이 실패하면 `PROTECTION_VERIFY_FAILED` block이 남고, 새 entry / scale-in 실행은 막습니다. 보호 복구가 반복 실패하면 `DEGRADED_MANAGE_ONLY`로 내려가고, 필요 시 emergency exit 경로가 별도로 시도됩니다.
- 운영자 화면에서는 이 상태를 `operating_state`, `guard_mode_reason_code`, `guard_mode_reason_message`, `sync_freshness_summary`, `reconciliation_summary`로 확인합니다.

## Holding Profile Risk Overlay

- `scalp`는 기본 신규 진입 프로필입니다.
- `swing`은 `meta_gate=pass`가 필요하고, intraday 정렬이 유지되어야 하며 leverage / notional / risk budget을 더 보수적으로 줄입니다.
- `position`은 `meta_gate=pass`가 필요하고, strong higher timeframe alignment, breadth not weak, positive lead-lag, positive relative strength, severe derivatives headwind 없음이 모두 필요합니다.
- `breakout_confirm` 신규 진입은 기본적으로 scalp 전용으로 유지하며, 장기 holding profile에서는 별도 예외를 열지 않습니다.
- holding profile soft cap은 하드 리스크 한도 위에만 추가 적용되며, stale sync / protection / approval / daily loss 같은 하드 차단을 우회하지 않습니다.

Lead-lag context가 `unavailable`이면 신규 진입을 직접 완화하지 않습니다. 현재 구현은 candidate score와 AI context payload에 보수적으로 반영하고, `position` holding profile에서는 positive lead-lag가 없으면 `HOLDING_PROFILE_POSITION_LEAD_LAG_MISMATCH`로 막습니다. 일부 lead reference만 있는 partial 상태는 남은 reference로 점수를 계산하지만, 이를 강한 확인 근거로 과장하지 않습니다.

## Hard Stop Policy

- 최초 손절은 항상 deterministic hard stop입니다.
- AI는 break-even 이동, trailing tighten, partial reduce, stop profile 재조정 제안만 할 수 있습니다.
- AI는 hard stop 제거, stop widening, 무손절 유지, protection 없는 신규 진입 허용을 할 수 없습니다.
- `reduce`, `exit`, `reduce_only`, `protection recovery`, `emergency_exit`는 holding profile 차단과 구분되는 survival path로 유지합니다.

## Exchange Minimum Actionable Size

- 신규 진입 quantity는 risk 단계에서 exchange `min_notional`, `min_qty`, `step_size`를 우선 참고해 실행 가능 크기로 정규화합니다.
- headroom auto-resize 이후 quantity 또는 notional이 exchange minimum을 만족하지 못하면 `ENTRY_SIZE_BELOW_MIN_NOTIONAL`로 차단합니다.
- `approved_projected_notional`과 `approved_quantity`는 exchange-actionable 기준 값이며, execution 단계는 이 값을 넘어서는 silent upsize를 하지 않습니다.

즉 리스크 정책은 신규 진입을 막더라도 생존 경로까지 일괄 차단하지 않습니다.
