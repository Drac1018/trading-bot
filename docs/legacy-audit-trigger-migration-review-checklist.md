# Legacy Audit Trigger Migration / Archival Review Checklist

## 목적

이 문서는 legacy audit trigger row

- `open_position_recheck_due`
- `periodic_backstop_due`

에 대해 migration 또는 archival을 **실제로 진행할지 판단하기 전에** 확인해야 할 항목을 정리한 review checklist다.

이 문서는 구현 지시가 아니다.

- SQL migration 없음
- schema 변경 없음
- code diff 없음

관련 판단 문서:

- [docs/legacy-audit-trigger-migration-decision.md](/C:/my-trading-bot/docs/legacy-audit-trigger-migration-decision.md)
- [docs/audit-legacy-trigger-policy.md](/C:/my-trading-bot/docs/audit-legacy-trigger-policy.md)
- [docs/api.md](/C:/my-trading-bot/docs/api.md)

## Go / No-Go 판단 체크리스트

아래 항목이 모두 `Yes`일 때만 migration/archival 검토를 계속한다.

- [ ] 현재 UI/read-model 해석만으로는 운영 혼선을 더 이상 감당하기 어렵다.
- [ ] UI 밖 consumer(BI, export, compliance, external report)가 legacy semantics를 DB 레벨에서 직접 구분해야 한다.
- [ ] `trigger_reason / last_ai_trigger_reason` read-helper 유지 비용이 실제 운영 부담이 되기 시작했다.
- [ ] forensic raw 보존 전략이 migration 이후에도 명확하다.
- [ ] rollback 기준과 rollback 소유자가 정해져 있다.
- [ ] migration 대상 row 범위가 계수 가능하다.
- [ ] migration 후 검증 주체가 정해져 있다.

다음 중 하나라도 `No`면 현재 단계에서는 **보류(No-Go)** 가 기본값이다.

## Migration 전 확인 항목

### 1. Forensic 보존

- [ ] raw `trigger_reason` 원문을 그대로 보존할 위치가 정해져 있다.
- [ ] row 원문과 normalized semantics가 충돌할 때 어떤 값을 source of truth로 볼지 정해져 있다.
- [ ] audit trail의 시간 순서와 원문 payload가 깨지지 않는지 설명 가능하다.

### 2. Rollback

- [ ] rollback이 row-level인지 table-level인지 정해져 있다.
- [ ] rollback 시 어떤 필드가 원복되는지 문서화돼 있다.
- [ ] rollback window가 정해져 있다.
- [ ] rollback 판단자와 승인자가 정해져 있다.

### 3. Sampling

- [ ] migration 대상 row에서 샘플링 기준이 정해져 있다.
- [ ] `open_position_recheck_due`
- [ ] `periodic_backstop_due`
- [ ] current runtime trigger row
- [ ] mixed metadata row
- [ ] null / malformed payload row

### 4. UI consumer 영향

- [ ] decisions / overview / scheduler / audit UI에 추가 수정이 필요한지 확인했다.
- [ ] current runtime trigger와 legacy trigger badge/tone이 다시 섞이지 않는지 확인했다.
- [ ] helper 변경이 필요한 파일 범위를 식별했다.

### 5. BI / export 영향

- [ ] 기존 SQL / export / CSV consumer가 어떤 필드를 직접 읽는지 파악했다.
- [ ] legacy semantics가 현재 trigger처럼 집계되는 리포트가 있는지 확인했다.
- [ ] migration 후 지표 정의가 바뀌는 보고서가 있는지 확인했다.

### 6. Query 변경 범위

- [ ] ad-hoc query
- [ ] dashboard read-model
- [ ] audit API consumer
- [ ] BI / reporting query
- [ ] external export

위 경로별로 변경 필요 여부를 명시했다.

## Migration 후 검증 항목

### 1. Data correctness

- [ ] raw field가 보존돼 있다.
- [ ] normalized / flag / archival 결과가 샘플 기준과 일치한다.
- [ ] legacy row 수와 current row 수가 예상 범위와 맞는다.
- [ ] null / malformed row가 누락되지 않았다.

### 2. Audit explainability

- [ ] 운영자가 row 하나를 보고 `현재 runtime semantics`와 `historical semantics`를 구분 설명할 수 있다.
- [ ] 과거 row가 현재 정책처럼 다시 읽히지 않는다.
- [ ] source of truth 문서와 화면 해석이 충돌하지 않는다.

### 3. UI / consumer validation

- [ ] audit 화면
- [ ] decisions 화면
- [ ] overview / scheduler 해석 helper
- [ ] BI / export 샘플

에서 legacy/current semantics가 분리되어 보인다.

### 4. Query validation

- [ ] 기존 주요 query가 깨지지 않는다.
- [ ] 새로운 flag / normalized field / archival split이 있으면 query 문서가 갱신돼 있다.
- [ ] ad-hoc SQL에서 current / historical row를 쉽게 나눌 수 있다.

## Rollback 판단 기준

아래 중 하나라도 발생하면 rollback 후보로 본다.

- [ ] raw 원문이 손실되었거나 손실 가능성이 생겼다.
- [ ] current runtime trigger와 historical semantics가 다시 혼동된다.
- [ ] audit API / dashboard / export 결과가 샘플 기준과 어긋난다.
- [ ] BI / compliance 보고서 수치가 설명 불가능하게 달라진다.
- [ ] migration 후 query 복잡도가 예상보다 높아져 운영 부담이 커진다.
- [ ] rollback 비용보다 유지 비용이 더 커진다.

## 현재 프로젝트 기준 요약

현재 프로젝트 기준 기본 판단은 **지금은 보류(No-Go)** 다.

이유:

- 현재 단계의 우선순위는 실거래 코어 안정화다.
- 이미 UI/read-model은 `과거 정책 기록`으로 분리 해석된다.
- 지금은 raw를 건드릴 실익보다 forensic / 운영 리스크가 더 크다.
- 따라서 migration/archival은 별도 decision 트랙으로만 다루는 것이 맞다.
