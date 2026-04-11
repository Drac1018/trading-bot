# Agent Design

## Trading Decision AI

- 입력: market snapshot, feature snapshot, open positions, risk context
- 출력: `TradeDecision`
- 특징: 완전 결정론적 휴리스틱 기반 mock provider를 기본 사용

## Chief Review AI

- 입력: 최근 trade decision, risk result, system health, alerts
- 출력: `ChiefReviewSummary`
- 특징: 실행 여부가 아니라 운영 모드와 우선 대응을 제안

## Integration Planner AI

- 입력: 로그/리스크/스케줄 요약, 헬스 이벤트
- 출력: `IntegrationSuggestionBatch`
- 특징: 4시간 주기 배치 리뷰

## UI/UX AI

- 입력: UI feedback
- 출력: `UXSuggestionBatch`
- 특징: 12시간 주기 리뷰, 자동 배포 없음

## Product Improvement AI

- 입력: KPI, competitor notes, prior backlog
- 출력: `ProductBacklogBatch`
- 특징: 24시간 주기 리뷰, 정책 자동 변경 없음

