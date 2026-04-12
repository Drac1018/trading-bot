# Codex 초안과 자동 Resume

## Codex 프롬프트 초안

백로그 항목이 아직 미적용 상태이고 적용 이력이 없으면 시스템이 `Codex에 붙여넣을 초안 프롬프트`를 로컬에서 자동 생성합니다.

특징:

- Codex API를 호출하지 않습니다.
- OpenAI 추가 호출도 하지 않습니다.
- 현재 backlog 제목, 문제, 제안 내용, 근거, 연결된 사용자 요청을 묶어 한 번에 실행 가능한 초안으로 만듭니다.
- 이미 적용되었거나 `verified` 상태인 backlog에는 초안을 만들지 않습니다.

관련 API:

- `GET /api/backlog`
- `GET /api/backlog/{backlog_id}`
- `GET /api/backlog/{backlog_id}/codex-draft`

화면에서는 backlog 카드 안에서 바로 초안을 복사할 수 있습니다.

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
