# Agent Design

현재 제품 범위는 **실거래 코어 운영**에 필요한 AI 역할만 유지한다.
기획·개선·UI 제안 성격의 보조 워크플로우는 현재 운영 범위 밖이다.

## 현재 유지 대상

### Trading Decision AI

- 입력: market snapshot, feature snapshot, open positions, risk context
- 출력: `TradeDecision`
- 역할: 방향과 아이디어를 제안하지만 실행 권한은 없다.

### Chief Review AI

- 입력: 최근 trade decision, risk result, system health, alerts
- 출력: `ChiefReviewSummary`
- 역할: 운영 모드, 우선 대응, 주의 상태를 요약한다.

## 현재 범위 밖

아래 역할은 장기 확장 아이디어로만 남기며, 현재 제품의 활성 범위나 기본 운영 워크플로우로 보지 않는다.

- Integration Planner AI
- UI/UX AI
- 추가 개선 기획 AI
