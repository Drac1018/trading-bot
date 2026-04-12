# Execution Flow

현재 저장소의 기본 흐름은 **시장/계좌 상태 수집 -> AI 판단 -> `risk_guard` 검증 -> 주문 실행 -> 감사/동기화**입니다.

## 실거래 결정 루프

1. Binance 시장 데이터를 수집해 `market_snapshots`에 저장
2. 특징을 계산해 `feature_snapshots`에 저장
3. Trading Decision AI가 구조화된 `TradeDecision` 생성
4. 스키마 검증 통과 여부 확인
5. `risk_guard`가 아래를 최종 검증
   - pause 상태
   - 계좌 / 시장 데이터 신뢰성
   - 레버리지 / 거래당 리스크 / 일일 손실 한도
   - 손절 / 익절 유효성
   - 슬리피지
   - live approval / 환경 게이트
6. 허용되면 실행 계층이 Binance 주문 생성
7. 주문 / 체결 / 포지션 / PnL / 감사 로그 갱신
8. 실패하면 alert / audit / health event 기록

## 상태 제어 경계

- `settings.py`
  - pause / resume
  - live arm / disarm
  - approval window
  - 운영 설정 직렬화
- `risk.py`
  - 최종 허용 / 차단 관문
- `execution.py`
  - 실주문 생성
  - 보호 주문 생성
  - 동기화 / 거절 처리
- `audit.py`
  - alert / audit event / health event 기록

## 중요한 보수 규칙

- AI가 `long` 또는 `short`를 제안해도 `risk_guard`가 막으면 주문은 나가지 않습니다.
- pause 상태에서는 신규 거래가 차단됩니다.
- 보호 주문 생성 실패는 시스템 중지 사유가 됩니다.
- 거래소 계좌 상태를 읽지 못하면 신규 진입보다 중지가 우선합니다.

## 스케줄러

- `decision_cycle_interval_minutes`
  - 시장 갱신 및 거래 의사결정 루프
- `4h`, `12h`, `24h`
  - Integration Planner, UI/UX, Product Improvement 배치 리뷰

`1h` 창은 중복 AI 호출을 만들지 않도록 현재는 시장 새로고침/상태 점검 성격으로 제한됩니다.
