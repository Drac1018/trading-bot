# 운영자용 AI 판단 흐름

이 문서는 현재 코드에서 확인 가능한 대시보드 표시 기준을 설명합니다. 실제 거래 판단 기준은 `risk.py`, 주문 실행은 `execution.py`, 운영자 표시 조립은 `dashboard.py`가 기준입니다.

## 전체 흐름

1. 시장, 계좌, 포지션, 오픈 주문, 보호 주문 상태를 수집합니다.
2. 스케줄러 또는 이벤트 기반 판단 주기가 실행됩니다.
3. 현재 후보가 있으면 AI 호출 이벤트가 생성될 수 있고, 이벤트가 없거나 중복이면 AI 호출이 스킵될 수 있습니다.
4. AI가 호출되면 거래 의도와 판단 payload를 생성합니다.
5. 응답 schema 검증을 통과한 판단만 다음 단계로 전달됩니다.
6. `risk_guard`가 deterministic hard gate로 최종 승인 또는 차단을 결정합니다.
7. risk 승인 후에만 execution 경로가 실제 주문 제출을 검토합니다.
8. 조건부 진입이면 즉시 주문이 아니라 `pending_entry_plan`으로 대기합니다.
9. audit/dashboard는 각 단계를 별도 상태로 표시합니다.

## 역할 분리

- AI: 거래 의도와 판단을 제안합니다. 실주문 권한은 없습니다.
- `risk_guard`: 신규 진입, 축소, 청산, survival path의 최종 deterministic 승인/차단 게이트입니다.
- `execution`: `risk_guard`가 승인한 실행만 처리합니다. 주문 제출, 체결, 제출 결과 불명확 상태를 기록합니다.
- `protection recovery`: AI와 분리된 deterministic 보호 주문 점검/복구 경로입니다.
- dashboard: AI 판단, risk 결과, pending plan, protection 상태, execution 상태를 운영자가 구분해서 보게 합니다.

## 운영자가 봐야 할 상태

- AI 호출됨/스킵됨: AI가 실제 provider까지 호출됐는지, 중복/이벤트 없음/사전 조건 때문에 생략됐는지 봅니다.
- risk 승인/차단: AI 판단과 별도로 `risk_guard.allowed` 및 reason code를 봅니다.
- 차단 사유 그룹: 데이터 신선도, 노출 한도, 승인 상태, 보호 주문, 진입 조건, 기타로 나누어 봅니다. 원문 reason code는 숨기지 않습니다.
- pending entry plan: AI 판단이 생성됐더라도 즉시 주문이 아니라 조건부 진입 대기일 수 있습니다.
- protection review/recovery: 보호 주문 누락, 미검증, 복구 진행, 복구 실패는 운영/복구 상태로 봅니다. AI가 보호 주문을 직접 생성하거나 복구하는 의미가 아닙니다.
- live approval/arm 상태: approval window, live arm/disarm, live trading disabled 상태는 신규 진입 차단 원인이 될 수 있습니다.
- stale/incomplete 상태: 계좌, 포지션, 오픈 주문, 시장 데이터, 보호 주문 검증 상태가 stale/incomplete이면 신규 진입이 차단될 수 있습니다.

## 대시보드 타임라인 의미

운영자 대시보드의 AI 판단 흐름 카드는 다음 네 단계를 분리해서 보여줍니다.

- 이벤트: 생성됨, 없음, 기록 있음, 확인 불가
- AI: 호출됨, 스킵됨, 대기, 확인 불가
- Risk: 승인, 차단, 미평가
- 실행: 주문 실행, 주문 제출, 조건부 대기, 실행 차단, 실행 없음

`Risk: 차단`은 deterministic risk gate에서 막힌 상태입니다. `실행: 실행 없음`은 주문/체결 기록이 없다는 뜻이며, risk 차단과 같은 의미가 아닙니다.

## Pending Entry Plan

`pending_entry_plan`은 체결이나 즉시 주문이 아닙니다.

- `armed`: 조건부 진입 대기입니다.
- `triggered`: 조건 충족 후 실행 경로로 넘어간 기록입니다. 실제 주문/체결 여부는 execution 상태에서 별도로 확인합니다.
- `canceled`: 조건부 진입이 취소된 상태입니다.
- `expired`: 조건 충족 전 만료된 상태입니다.

운영자 문구는 다음 의미를 유지해야 합니다.

- AI 판단은 생성되었지만 즉시 주문이 아닙니다.
- 설정된 조건이 충족될 때까지 실행 대기 상태입니다.
- risk/execution 재검증 후에만 실제 주문으로 이어질 수 있습니다.

## Protection Review / Recovery

보호 주문 관련 문구는 AI 판단과 protection recovery를 혼동시키면 안 됩니다.

- AI는 보호 주문을 직접 생성/복구하지 않습니다.
- protection recovery는 deterministic 운영/복구 경로입니다.
- 보호 주문이 확인되지 않으면 신규 진입은 차단될 수 있습니다.
- 보호 주문 복구 실패나 미검증은 운영 이슈로 보고, AI 판단 결과와 분리해서 확인합니다.

## 주의 문구

- AI 판단은 실주문 권한이 아닙니다.
- `risk_guard` 차단 시 신규 주문은 실행되지 않습니다.
- `pending_entry_plan`은 체결이 아니라 조건부 대기입니다.
- 보호 주문 누락/미확인은 신규 진입 차단 또는 deterministic 복구 경로로 이어질 수 있습니다.
- dashboard 문구는 실제 코드 동작을 기준으로 유지해야 합니다.

## 코드 근거

- `backend/trading_mvp/services/dashboard.py`: 운영자 대시보드 payload 조립, AI/risk/execution/pending/protection 표시 데이터 연결
- `backend/trading_mvp/services/risk.py`: deterministic risk_guard 승인/차단 기준
- `backend/trading_mvp/services/execution.py`: 승인된 intent의 주문 실행 및 protection verification 처리
- `backend/trading_mvp/services/orchestrator.py`: decision cycle, AI trigger, risk 호출 흐름
- `backend/trading_mvp/services/scheduler.py`: 주기 실행 및 AI 호출/스킵 이벤트 흐름
- `frontend/components/dashboard-views.tsx`: 운영자용 AI 판단 흐름 카드, risk reason 그룹, pending/protection 문구 표시
