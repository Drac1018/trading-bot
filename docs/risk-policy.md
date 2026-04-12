# Risk Policy

현재 리스크 가드는 [C:/my-trading-bot/backend/trading_mvp/services/risk.py](C:/my-trading-bot/backend/trading_mvp/services/risk.py)에 구현되어 있다.
이 문서는 실거래 신규 진입 전에 강제되는 결정론적 하드 규칙만 짧게 정리한다.

## 하드 리스크 기준

- BTC 최대 레버리지: `5x`
- 메이저 알트 최대 레버리지: `3x`
- 일반 알트 최대 레버리지: `2x`
- 1회 거래 최대 손실: `2%`
- 일일 손실 한도: `5%`
- 연속 손실 `3회` 이상이면 신규 진입 제한
- stale / incomplete market data에서는 신규 진입 차단
- 보호 주문 누락, pause, degraded manage only 상태에서는 신규 진입 차단
- 계좌 / 시장 상태를 신뢰할 수 없으면 신규 진입 차단

## 노출도 하드 게이트

신규 진입과 추가 진입은 아래 노출도 한도를 넘으면 차단된다.
한도는 settings에 저장되지만, 런타임에서는 더 보수적인 하드 상한으로 clamp된다.

- `max_gross_exposure_pct`
  - 총 포지션 명목가 / equity
- `max_largest_position_pct`
  - 가장 큰 단일 심볼 포지션 명목가 / equity
- `max_directional_bias_pct`
  - long 또는 short 중 더 큰 방향 노출 / equity
- `max_same_tier_concentration_pct`
  - 동일 리스크 tier 포지션 명목가 합 / equity

현재 기본 하드 상한:

- 총 노출도: `3.0`
- 최대 단일 포지션: `1.5`
- 방향 편중: `2.0`
- 동일 tier 집중도: `2.5`

## 차단 reason code

노출도 하드 게이트는 아래 reason code로 기록된다.

- `GROSS_EXPOSURE_LIMIT_REACHED`
- `LARGEST_POSITION_LIMIT_REACHED`
- `DIRECTIONAL_BIAS_LIMIT_REACHED`
- `SAME_TIER_CONCENTRATION_LIMIT_REACHED`

## 허용되는 관리 경로

노출도 초과는 신규 진입만 막는다.
아래 경로는 계속 허용된다.

- `reduce`
- `exit`
- 보호 주문 복구
- `reduce_only`
- `emergency_exit`

즉 노출도 하드 게이트는 포지션 생존 경로를 막지 않는다.
