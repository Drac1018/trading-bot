# Architecture

현재 저장소는 멀티 에이전트 자동매매 플랫폼 전체를 크게 확장하기보다, **실거래에 안전한 Binance Futures 코어**를 우선 안정화하는 구조로 정리되어 있습니다.

## 현재 핵심 경계

### 1. AI 판단

- 위치: `backend/trading_mvp/services/agents.py`
- 역할:
  - Trading Decision AI
  - Chief Review AI
  - Integration Planner AI
  - UI/UX AI
  - Product Improvement AI
- 원칙:
  - AI는 구조화된 출력만 생성
  - 실행 권한은 없음

### 2. risk_guard

- 위치: `backend/trading_mvp/services/risk.py`
- 역할:
  - 결정론적 최종 허용 / 차단
  - 하드 리스크 기준 적용
  - 노출도 계산 및 기록

### 3. 주문 실행

- 위치: `backend/trading_mvp/services/execution.py`
- 역할:
  - Binance 실주문
  - 보호 주문 생성
  - 주문 / 체결 / 포지션 동기화
  - 실패 시 alert / audit / pause 연동

### 4. 상태 관리 / 운영 제어

- 위치: `backend/trading_mvp/services/settings.py`
- 관련 보조: `pause_control.py`
- 역할:
  - pause / resume
  - live arm / disarm
  - approval window
  - AI / 시장데이터 / 심볼 / 주기 설정

### 5. 감사 / 추적

- 위치: `backend/trading_mvp/services/audit.py`
- 역할:
  - audit event
  - alert
  - health event

### 6. 운영 UI

- 위치: `frontend/`
- 성격:
  - 홍보 사이트가 아니라 운영자용 대시보드
  - 실제 백엔드 상태를 그대로 보여주는 것이 우선

## 현재 설계 원칙

- AI 판단과 실주문은 분리
- pause / manual control / audit trail 유지
- 시장/계좌 상태를 신뢰할 수 없으면 신규 진입 차단
- 리스크 가드는 런타임 하드 정책을 절대 우회하지 않음
- 테스트와 문서는 현재 실거래 코어 기준으로 유지

## 향후 확장

향후 멀티 에이전트 플랫폼으로 더 확장하더라도, 아래 경계는 유지하는 것이 전제입니다.

- AI 판단
- risk_guard
- execution
- settings / control
- audit / health
