# 자동 Resume 메모

## Pause 원인 분리

`trading_paused`는 이제 단순 boolean 외에 아래 메타데이터를 함께 가집니다.

- `pause_reason_code`
- `pause_origin`
- `pause_reason_detail`
- `pause_triggered_at`
- `auto_resume_after`

이 정보는 운영자가 왜 중지됐는지와 자동 복귀 대상인지 구분하는 데 사용됩니다.

## 자동 Resume 정책

자동 resume은 모든 pause에 대해 허용하지 않습니다.

현재 화이트리스트:

- `EXCHANGE_ACCOUNT_STATE_UNAVAILABLE`

즉, 거래소 계정 상태를 일시적으로 읽지 못해 시스템이 안전 중지한 경우에만 자동 복귀를 시도합니다.

다음 조건을 모두 만족해야 자동 resume 됩니다.

- 환경 게이트 켜짐
- 실거래 활성화 켜짐
- 수동 승인 정책 유지
- 승인 창이 아직 열려 있음
- Binance API Key / Secret 존재
- 거래소 계정 조회 성공
- 열린 포지션에 필요한 보호 주문이 누락되지 않음

아래 같은 pause는 자동 resume 대상이 아닙니다.

- `MANUAL_USER_REQUEST`
- `PROTECTIVE_ORDER_FAILURE`

즉, 사람이 직접 멈춘 경우나 보호 주문 생성 실패처럼 더 위험한 경우는 운영자가 직접 확인 후 해제해야 합니다.
