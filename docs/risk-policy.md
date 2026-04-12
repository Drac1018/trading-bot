# Risk Policy

현재 코드베이스의 리스크 가드는 `backend/trading_mvp/services/risk.py`에 구현되어 있습니다.  
문서의 목적은 운영 상의 기준과 런타임 강제 규칙을 짧게 정리하는 것입니다.

## 하드 정책

- BTC 최대 레버리지: `5x`
- 메이저 알트 최대 레버리지: `3x`
- 일반 알트 최대 레버리지: `2x`
- 1회 거래 최대 손실: `2%`
- 일일 손실 한도: `5%`
- 연속 손실 `3회` 이후 신규 진입 보수 제한
- 손절/익절 누락 또는 방향 오류 시 신규 진입 차단
- stale / incomplete market data 시 신규 진입 차단
- 슬리피지 한도 초과 시 실행 차단 또는 경고
- 계좌/시장 상태를 신뢰할 수 없으면 신규 진입 차단
- 결정론적 정책은 항상 AI보다 우선

## 심볼군 분류

- BTC: `BTCUSDT`
- 메이저 알트: `ETHUSDT`, `BNBUSDT`, `SOLUSDT`, `XRPUSDT`, `ADAUSDT`, `DOGEUSDT`
- 그 외 추적 심볼: 일반 알트

실제 적용 레버리지는 아래 둘 중 더 보수적인 값입니다.

- 운영자가 설정한 전역 상한 `max_leverage`
- 심볼군 하드 캡

## 노출도 계산

리스크 가드는 차단 사유 외에도 아래 노출도 지표를 계산해 `risk_checks.payload`에 남깁니다.

- 총 gross exposure
- long / short 노출 비중
- 방향 편중 비율
- 현재 심볼 집중도
- 동일 심볼군 집중도
- 가장 큰 단일 포지션 비율
- 열린 포지션 수

이 지표는 현재는 추적과 운영 판단 보조가 목적이며, 이후 위험 한도 규칙이 추가되면 차단 조건으로 확장할 수 있습니다.

## pause / resume

- `manual_pause`는 자동 resume 대상이 아닙니다.
- 시스템이 계좌 상태를 읽지 못해 중지한 경우에만 제한적으로 auto resume 후보가 됩니다.
- 보호 주문 실패(`PROTECTIVE_ORDER_FAILURE`)는 자동 resume 대상이 아닙니다.

세부 정책은 `docs/codex-drafts-and-auto-resume.md`를 함께 참고하세요.
